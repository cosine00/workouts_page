"""Sync activities from Intervals.icu into run_page's local database.

Intervals.icu 保存的是各设备/平台上传的原始文件，脚本通过 REST API 拉取活动列表，
再逐个下载原始 FIT/GPX/TCX，解析后写入 run_page/data.db，最后重建 activities.json。

用法:
    python run_page/intervals_icu_sync.py <athlete_id> <api_key> \
        --start-date 2026-11-01 --types run,ride,hike

athlete_id / api_key 在 Intervals.icu 的 Settings -> Developer Settings 中获取。
API 使用 HTTP Basic 认证，用户名为固定的 "API_KEY"，密码为你的 API Key。
"""

import argparse
import gzip
import json
import os
import shutil
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import eviltransform
import gpxpy
import requests
from config import FOLDER_DICT, JSON_FILE, SQL_FILE
from gpxtrackposter.track_loader import load_fit_file, load_gpx_file, load_tcx_file
from requests.auth import HTTPBasicAuth

from generator import Generator

BASE_URL = "https://intervals.icu/api/v1"
SOURCE_NAME = "Intervals.icu"

# Intervals.icu 的运动类型 -> 本项目的类型族
TYPE_FAMILIES = {
    "run": ["Run", "TrailRun", "VirtualRun"],
    "ride": [
        "Ride",
        "VirtualRide",
        "GravelRide",
        "MountainBikeRide",
        "EBikeRide",
        "EMountainBikeRide",
    ],
    "hike": ["Hike", "Walk", "Snowshoe"],
    "swim": ["Swim"],
    "row": ["Rowing", "VirtualRow"],
    "ski": ["NordicSki", "AlpineSki", "BackcountrySki", "RollerSki"],
}
DEFAULT_TYPES = "run"

LOADERS = {"gpx": load_gpx_file, "tcx": load_tcx_file, "fit": load_fit_file}
TCX_NAMESPACE = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"

# 去重容差：同一次运动从不同来源同步时，开始时间与距离会略有差异
START_TOLERANCE_SECONDS = 180
DISTANCE_TOLERANCE_METERS = 200
DISTANCE_TOLERANCE_RATIO = 0.02


class IntervalsICU:
    def __init__(self, athlete_id, api_key):
        self.athlete_id = athlete_id
        self.api_key = api_key
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth("API_KEY", api_key)
        self.session.headers["Accept"] = "application/json"

    def get_activities(self, oldest, newest):
        url = (
            f"{BASE_URL}/athlete/{self.athlete_id}/activities"
            f"?oldest={oldest}&newest={newest}"
        )
        response = self.session.get(url, timeout=60)
        if response.status_code in (401, 403):
            raise SystemExit(
                "Intervals.icu 认证失败：请检查 athlete id 与 api key 是否正确"
                "（Settings -> Developer Settings）"
            )
        response.raise_for_status()
        return response.json()

    def download_activity_file(self, activity_id, file_type, output_folder):
        url = f"{BASE_URL}/activity/{activity_id}/file"
        numeric_id = str(activity_id).lstrip("i")
        output_path = os.path.join(output_folder, f"{numeric_id}.{file_type}")

        try:
            response = self.session.get(url, timeout=120)
            response.raise_for_status()
            content = response.content
            # 部分活动会返回 gzip 压缩内容（magic bytes: 1f 8b）
            if content[:2] == bytes([0x1F, 0x8B]):
                content = gzip.decompress(content)

            with open(output_path, "wb") as f:
                f.write(content)

            return output_path
        except Exception as e:  # noqa: BLE001
            print(f"  Warning: 下载 {activity_id} 失败: {e}")
            return None


def family_of(sport_type):
    """返回运动类型所属的族名，例如 MountainBikeRide -> ride。"""
    for family, members in TYPE_FAMILIES.items():
        if sport_type in members:
            return family
    lowered = str(sport_type).lower()
    for family in TYPE_FAMILIES:
        if lowered.startswith(family):
            return family
    return lowered


def parse_datetime(value):
    if not value:
        return None
    text = str(value).strip().replace("T", " ").replace("Z", "")
    if "+" in text:
        text = text.split("+")[0]
    if "." in text:
        text = text.split(".")[0]
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def load_existing_activities(db_path):
    """从数据库读出已有活动索引：(开始时间, 距离, run_id, source, name)。"""
    if not os.path.exists(db_path):
        return []
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "select start_date_local, distance, run_id, source, name from activities"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()

    existing = []
    for start, distance, run_id, source, name in rows:
        start_dt = parse_datetime(start)
        if start_dt is None:
            continue
        existing.append((start_dt, float(distance or 0), run_id, source or "", name or ""))
    return existing


def find_duplicate(activity, existing):
    """查找数据库中已存在的同一条活动（同一时刻、距离接近）。

    命中时返回该行的索引元组，未命中返回 None。
    """
    start_dt = parse_datetime(activity.get("start_date_local"))
    if start_dt is None:
        return None
    distance = float(activity.get("distance") or 0)
    for other in existing:
        other_start, other_distance = other[0], other[1]
        if abs((other_start - start_dt).total_seconds()) > START_TOLERANCE_SECONDS:
            continue
        # 本脚本自己写进来的记录：开始时间对上就算同一条，
        # 避免 API 距离与文件解析距离有偏差时被误判成新活动
        if other[3] == SOURCE_NAME:
            return other
        tolerance = max(
            DISTANCE_TOLERANCE_METERS,
            max(distance, other_distance) * DISTANCE_TOLERANCE_RATIO,
        )
        if abs(distance - other_distance) <= tolerance:
            return other
    return None


def correct_gpx_gcj02(file_path):
    """把 GPX 中的 GCJ-02 坐标转换为 WGS-84。"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            gpx = gpxpy.parse(f)

        count = 0
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    wgs_lat, wgs_lng = eviltransform.gcj2wgs_exact(
                        point.latitude, point.longitude
                    )
                    point.latitude = wgs_lat
                    point.longitude = wgs_lng
                    count += 1

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(gpx.to_xml())

        print(f"  GCJ-02 -> WGS-84: 已纠正 {count} 个坐标点 {file_path}")
    except Exception as e:  # noqa: BLE001
        print(f"  Warning: GPX 坐标纠偏失败 {file_path}: {e}")


def correct_tcx_gcj02(file_path):
    """把 TCX 中的 GCJ-02 坐标转换为 WGS-84。"""
    try:
        ET.register_namespace("", TCX_NAMESPACE)

        tree = ET.parse(file_path)
        root = tree.getroot()
        namespace = TCX_NAMESPACE

        count = 0
        for position in root.iter(f"{{{namespace}}}Position"):
            lat_el = position.find(f"{{{namespace}}}LatitudeDegrees")
            lng_el = position.find(f"{{{namespace}}}LongitudeDegrees")
            if lat_el is None or lng_el is None:
                continue
            lat = float(lat_el.text)
            lng = float(lng_el.text)
            wgs_lat, wgs_lng = eviltransform.gcj2wgs_exact(lat, lng)
            lat_el.text = str(wgs_lat)
            lng_el.text = str(wgs_lng)
            count += 1

        tree.write(file_path, xml_declaration=True, encoding="UTF-8")
        print(f"  GCJ-02 -> WGS-84: 已纠正 {count} 个坐标点 {file_path}")
    except Exception as e:  # noqa: BLE001
        print(f"  Warning: TCX 坐标纠偏失败 {file_path}: {e}")


def correct_fit_gcj02(file_path):
    """把 FIT 中的 GCJ-02 坐标转换为 WGS-84。"""
    try:
        from fit_tool.fit_file import FitFile
        from fit_tool.profile.messages.record_message import RecordMessage

        fit = FitFile.from_file(file_path)
        count = 0
        for record in fit.records:
            message = record.message
            if isinstance(message, RecordMessage) and (
                message.position_lat is not None and message.position_long is not None
            ):
                wgs_lat, wgs_lng = eviltransform.gcj2wgs_exact(
                    message.position_lat, message.position_long
                )
                message.position_lat = wgs_lat
                message.position_long = wgs_lng
                count += 1
        fit.crc = None  # 重置 CRC，写出时重新计算
        fit.to_file(file_path)
        print(f"  GCJ-02 -> WGS-84: 已纠正 {count} 个坐标点 {file_path}")
    except Exception as e:  # noqa: BLE001
        print(f"  Warning: FIT 坐标纠偏失败 {file_path}: {e}")


def correct_file_gcj02(file_path, file_type):
    if file_type == "gpx":
        correct_gpx_gcj02(file_path)
    elif file_type == "tcx":
        correct_tcx_gcj02(file_path)
    elif file_type == "fit":
        correct_fit_gcj02(file_path)


def round_timedelta(value):
    """把 timedelta 抹到整秒，避免库里出现 "0:43:05.408000" 这种值。"""
    if isinstance(value, timedelta):
        return timedelta(seconds=round(value.total_seconds()))
    return value


def resolve_types(types_option, sync_all):
    if sync_all:
        return None
    families = [
        item.strip().lower() for item in str(types_option).split(",") if item.strip()
    ]
    return set(families) or {DEFAULT_TYPES}


def select_candidates(activities, families):
    candidates = []
    for activity in activities:
        sport_type = activity.get("type")
        if not sport_type:
            continue
        if families is not None and family_of(sport_type) not in families:
            continue
        # 多数设备上传的活动都带原始文件；接口未返回 file_type 时按 fit 兜底
        file_type = str(activity.get("file_type") or "fit").lower()
        if file_type not in FOLDER_DICT:
            continue
        candidates.append((activity, file_type))
    return candidates


def run():
    parser = argparse.ArgumentParser(
        description="Sync activities from Intervals.icu"
    )
    parser.add_argument(
        "athlete_id",
        nargs="?",
        help="Intervals.icu athlete id, e.g. i123456（也可用环境变量 INTERVALS_ICU_ATHLETE_ID）",
    )
    parser.add_argument(
        "api_key",
        nargs="?",
        help="Intervals.icu API key（也可用环境变量 INTERVALS_ICU_API_KEY）",
    )
    parser.add_argument(
        "--start-date",
        default="2015-01-01",
        help="只同步该日期之后的活动 (YYYY-MM-DD, 默认 2015-01-01)",
    )
    parser.add_argument(
        "--types",
        default=DEFAULT_TYPES,
        help="要同步的运动类型族，逗号分隔，可选 run/ride/hike/swim/row/ski，默认 run",
    )
    parser.add_argument(
        "--all",
        dest="sync_all",
        action="store_true",
        help="同步所有运动类型（忽略 --types）",
    )
    parser.add_argument(
        "--gcj02",
        action="store_true",
        help="把下载文件中的 GCJ-02 坐标转换为 WGS-84（国内设备数据需要）",
    )
    parser.add_argument(
        "--no-dedupe",
        dest="dedupe",
        action="store_false",
        help="关闭与数据库已有活动的去重检查",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="对已存在的同一条活动原地覆写（保留原 run_id，只更新该行数据）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印每条活动将如何处理，不下载、不写库、不写 JSON",
    )
    options = parser.parse_args()

    athlete_id = options.athlete_id or os.getenv("INTERVALS_ICU_ATHLETE_ID")
    api_key = options.api_key or os.getenv("INTERVALS_ICU_API_KEY")
    if not athlete_id or not api_key:
        parser.error(
            "需要 athlete_id 与 api_key（或设置环境变量 "
            "INTERVALS_ICU_ATHLETE_ID / INTERVALS_ICU_API_KEY）"
        )

    today = datetime.now().strftime("%Y-%m-%d")
    families = resolve_types(options.types, options.sync_all)
    client = IntervalsICU(athlete_id, api_key)

    print(f"从 Intervals.icu 拉取 {options.start_date} ~ {today} 的活动 ...")
    activities = client.get_activities(oldest=options.start_date, newest=today)
    print(f"接口返回 {len(activities)} 条活动")

    candidates = select_candidates(activities, families)
    print(f"过滤运动类型后待处理 {len(candidates)} 条")
    if not candidates:
        print(
            "没有符合条件的活动：请确认 Intervals.icu 中确实有该日期之后的活动，"
            "并检查 --start-date / --types 参数"
        )

    existing = load_existing_activities(SQL_FILE) if options.dedupe else []
    generator = Generator(SQL_FILE)

    backup_done = False

    def ensure_backup():
        """第一次真正覆写数据库前做一次备份，普通新增不需要。"""
        nonlocal backup_done
        if backup_done:
            return
        backup_done = True
        if not os.path.exists(SQL_FILE):
            return
        backup_path = f"{SQL_FILE}.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(SQL_FILE, backup_path)
        print(f"已备份数据库到 {backup_path}")

    downloaded = 0
    synced = 0
    overwritten = 0
    skipped_duplicate = 0
    already_synced = 0
    skipped_failed = 0
    total = len(candidates)

    for n, (activity, file_type) in enumerate(candidates, start=1):
        numeric_id = str(activity["id"]).lstrip("i")
        output_folder = FOLDER_DICT[file_type]
        output_path = os.path.join(output_folder, f"{numeric_id}.{file_type}")
        label = (
            f"{activity.get('start_date_local')} {activity.get('type')} "
            f"{round(float(activity.get('distance') or 0)) / 1000:.2f}km "
            f"({activity['id']})"
        )

        overwrite_run_id = None
        if options.dedupe:
            matched = find_duplicate(activity, existing)
            if matched:
                if matched[3] == SOURCE_NAME:
                    if os.path.exists(output_path):
                        # 已是本脚本同步过的记录且文件在本地，无需重复解析
                        already_synced += 1
                        continue
                    # 本地文件丢了：重新下载并原地更新，不新增行
                    overwrite_run_id = matched[2]
                elif not options.overwrite:
                    skipped_duplicate += 1
                    print(f"跳过（已存在 {matched[3]} run_id={matched[2]}）{label}")
                    continue
                else:
                    overwrite_run_id = matched[2]
                    print(
                        f"覆盖已有记录 run_id={overwrite_run_id} "
                        f"source={matched[3]} name={matched[4]} <- {label}"
                    )

        if options.dry_run:
            if overwrite_run_id is not None:
                overwritten += 1
            else:
                synced += 1
                print(f"新增 {label}")
            continue

        if not os.path.exists(output_path):
            print(f"下载 {activity['id']} ({n}/{total}) ...")
            saved_path = client.download_activity_file(
                activity["id"], file_type, output_folder
            )
            if not saved_path:
                skipped_failed += 1
                time.sleep(0.5)
                continue
            downloaded += 1
            output_path = saved_path
            if options.gcj02:
                correct_file_gcj02(output_path, file_type)

        loader = LOADERS.get(file_type, load_gpx_file)
        title_dict = {numeric_id: activity["name"]} if activity.get("name") else {}
        try:
            track = loader(output_path, title_dict)
            track.source = SOURCE_NAME
            named_tuple = track.to_namedtuple()
        except Exception as e:  # noqa: BLE001
            print(f"  Warning: 解析 {output_path} 失败: {e}")
            skipped_failed += 1
            continue

        # FIT 解析出的时间是带微秒的 timedelta，统一抹到整秒，
        # 与库里其它来源（"0:43:05"）保持一致
        rounded = {
            field: round_timedelta(getattr(named_tuple, field))
            for field in ("moving_time", "elapsed_time")
            if hasattr(named_tuple, field)
        }
        if rounded:
            named_tuple = named_tuple._replace(**rounded)

        if overwrite_run_id is not None:
            # 复用原有 run_id，db 层就会走 update 分支而不是新增
            ensure_backup()
            named_tuple = named_tuple._replace(id=overwrite_run_id)
            overwritten += 1
        else:
            synced += 1

        generator.sync_from_app([named_tuple])

        if overwrite_run_id is None and track.start_time_local:
            start_dt = track.start_time_local
            if start_dt.tzinfo is not None:
                start_dt = start_dt.replace(tzinfo=None)
            existing.append(
                (start_dt, float(track.length or 0), named_tuple.id, SOURCE_NAME, "")
            )

        time.sleep(0.5)

    if options.dry_run:
        print(
            f"预览结束（未写入任何内容）：新增 {synced} 条，将覆盖 {overwritten} 条，"
            f"跳过重复 {skipped_duplicate} 条，已同步 {already_synced} 条"
        )
        return

    if synced or overwritten:
        activities_list = generator.load()
        with open(JSON_FILE, "w") as f:
            json.dump(activities_list, f, indent=0)
        print(f"已更新 {JSON_FILE}，共 {len(activities_list)} 条活动")
    else:
        print("没有新增或变更活动，跳过 activities.json 更新")

    print(
        f"完成：下载 {downloaded} 个文件，新增 {synced} 条，覆写 {overwritten} 条，"
        f"跳过重复 {skipped_duplicate} 条，已同步 {already_synced} 条，"
        f"失败 {skipped_failed} 条"
    )


if __name__ == "__main__":
    run()
