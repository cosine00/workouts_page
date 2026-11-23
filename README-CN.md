## note1: use v2.0 need change vercel setting from Gatsby to Vite

## note2: 2023.09.26 garmin need secret_string(and in Actions) get `python run_page/garmin_sync.py ${secret_string}` if cn `python run_page/garmin_sync.py ${secret_string} --is-cn`

## note3: 2024.08.19: Added `Elevation Gain` field

- For old data: To include `Elevation Gain` for past activities, perform a full reimport

# [打造个人户外运动主页](http://workouts.ben29.xyz)

![screenshot](https://user-images.githubusercontent.com/6956444/163125711-24d0ad99-490d-4c04-b89f-5b7fe776eb38.png)

简体中文 | [English](README.md)

本项目基于 [running_page](https://github.com/yihong0618/running_page/blob/master/README-CN.md) , 添加了支持多种运动类型。部署可参考原项目操作步骤

## 新增特性

1. 支持多种运动类型，如骑行、徒步、游泳
1. 支持 [Intervals.icu](#intervalsicu) 作为数据源
1. 支持 APP 数据获取
   - **[咕咚](#codoon咕咚)** (因咕咚限制单个设备原因，无法自动化)
   - **[行者](#行者)**
1. 支持 [自驾(Google 路书)](#自驾google路书) , 把自驾路线也展示在地图上

## 一些个性化选项

### 自定义运动颜色

- 修改骑行颜色: `src/utils/const.js` 里的 `RIDE_COLOR`

### 新增运动类型

- 修改 `scripts/config.py`, `TYPE_DICT` 增加类型映射关系, `MAPPING_TYPE` 里增加运动类型
- 修改 `src/utils/const.js`, 增加类型标题，并加入到 `RUN_TITLES`
- 修改 `src/utils/util.js` 里的 `colorFromType`, 增加 case 指定颜色; `titleForRun` 增加 case 指定类型标题
- 参考这个 [commit](https://github.com/ben-29/workouts_page/commit/f3a35884d626009d33e05adc76bbc8372498f317)
- 或 [留言](https://github.com/ben-29/workouts_page/issues/20)

---

### Intervals.icu

<details>
<summary>用 Intervals.icu 作为同步源</summary>

Intervals.icu 会保存各平台/设备上传的原始 FIT/GPX 文件，本项目通过其 REST API 拉取活动并写入本地数据库。

1. 登录 Intervals.icu，进入 `Settings -> Developer Settings`，记下 **Athlete ID**（形如 `i123456`）并创建 **API Key**。
2. 本地试跑（建议先用小范围日期验证）：

```python
python3(python) run_page/intervals_icu_sync.py ${athlete_id} ${api_key} --start-date 2026-11-01 --types run,ride,hike
```

参数说明：

- `--start-date`：只同步该日期之后的活动，避免与已有历史数据重复
- `--types`：运动类型族，逗号分隔，可选 `run` / `ride` / `hike` / `swim` / `row` / `ski`，默认 `run`
- `--all`：同步全部运动类型（忽略 `--types`）
- `--gcj02`：下载后把 GCJ-02 坐标纠偏为 WGS-84（数据来自华为等国内设备时需要）
- `--dry-run`：只打印每条活动会被「新增 / 覆盖 / 跳过」，不下载、不写库
- `--overwrite`：对已存在的同一条活动**原地覆写**（保留原 `run_id`，只更新该行），首次覆写前会自动备份 `data.db.bak-时间戳`
- `--no-dedupe`：关闭与数据库已有活动的去重检查

> 凭据也可以放在环境变量 `INTERVALS_ICU_ATHLETE_ID` / `INTERVALS_ICU_API_KEY` 里，命令行位置参数可省略。

3. 切换到 GitHub Actions 自动同步：

- 把 `.github/workflows/run_data_sync.yml` 里的 `RUN_TYPE` 改为 `intervals_icu`（已默认切换）
- 在仓库 `Settings -> Secrets and variables -> Actions` 添加两个 Secret：
  - `INTERVALS_ICU_ATHLETE_ID`
  - `INTERVALS_ICU_API_KEY`
- 如需调整同步范围，修改工作流顶部的 `INTERVALS_SYNC_START_DATE`、`INTERVALS_SYNC_TYPES`、`INTERVALS_GCJ02`

> 注意：旧的 Keep / Strava 数据仍保留在 `run_page/data.db` 与 `GPX_OUT` 中，不会被删除；新数据以 `source = Intervals.icu` 写入，脚本会按「开始时间 + 距离」自动跳过已存在的活动。

</details>

---

### Codoon（咕咚）

<details>
<summary>获取您的咕咚数据</summary>

```python
python3(python) run_page/codoon_sync.py ${your mobile or email} ${your password}
```

示例：

```python
python3(python) run_page/codoon_sync.py 13333xxxx xxxx
```

> 注：我增加了 Codoon 可以导出 gpx 功能, 执行如下命令，导出的 gpx 会加入到 GPX_OUT 中，方便上传到其它软件

```python
python3(python) run_page/codoon_sync.py ${your mobile or email} ${your password} --with-gpx
```

示例：

```python
python3(python) run_page/codoon_sync.py 13333xxxx xxxx --with-gpx
```

> 注：因为登录 token 有过期时间限制，我增加了 refresh_token&user_id 登陆的方式， refresh_token 及 user_id 在您登陆过程中会在控制台打印出来

![image](https://user-images.githubusercontent.com/6956444/105690972-9efaab00-5f37-11eb-905c-65a198ad2300.png)

示例：

```python
python3(python) run_page/codoon_sync.py 54bxxxxxxx fefxxxxx-xxxx-xxxx --from-auth-token
```

</details>

### 行者

<details>
<summary>获取您的行者数据</summary>

```python
python3(python) scripts/xingzhe_sync.py ${your mobile or email} ${your password}
```

示例：

```python
python3(python) scripts/xingzhe_sync.py 13333xxxx xxxx
```

> 注：我增加了 行者 可以导出 gpx 功能, 执行如下命令，导出的 gpx 会加入到 GPX_OUT 中，方便上传到其它软件

```python
python3(python) scripts/xingzhe_sync.py ${your mobile or email} ${your password} --with-gpx
```

示例：

```python
python3(python) scripts/xingzhe_sync.py 13333xxxx xxxx --with-gpx
```

> 注：因为登录 token 有过期时间限制，我增加了 refresh_token&user_id 登陆的方式， refresh_token 及 user_id 在您登陆过程中会在控制台打印出来

![image](https://user-images.githubusercontent.com/6956444/106879771-87c97380-6716-11eb-9c28-fbf70e15e1c3.png)

示例：

```python
python3(python) scripts/xingzhe_sync.py w0xxx 185000 --from-auth-token
```

</details>

### 自驾(Google 路书)

<details>
<summary>导入谷歌地图的KML路书</summary>

1. 使用 [谷歌地图](https://www.google.com/maps/d/) ，创建地图(路线放到同一个图层)
2. 把图层导出为 KML 文件
3. 把 kml 文件重命名为 `import.kml`, 放到 `scripts`目录
4. 修改`scripts/kml2polyline.py`, 填入路线相关信息

```
# TODO modify here
# 路线名称
track.name = "2020-10 西藏 Road Trip"
# 开始/结束时间 年月日时分
track.start_time = datetime(2020, 9, 29, 10, 0)
track.end_time = datetime(2020, 10, 10, 18, 0)
# 总路程
distance = 4000  # KM
# 总天数
days = 12
# 平均每天自驾时长
hours_per_day = 6
```

5. 控制台执行以下脚本

```python
python3(python) scripts\kml2polyline.py
```

</details>

# 致谢

- @[yihong0618](https://github.com/yihong0618) 特别棒的项目 [running_page](https://github.com/yihong0618/running_page) 非常感谢
