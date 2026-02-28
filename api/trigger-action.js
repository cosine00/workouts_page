export default async function handler(req, res) {
  // 1. 安全校验：检查链接中是否携带了正确的“暗号”
  const { secret } = req.query;
  if (secret !== process.env.TRIGGER_SECRET) {
    return res.status(401).send('暗号不对，拒绝执行');
  }

  // 2. 调用 GitHub API 触发 Action
  const response = await fetch(
    `https://api.github.com/repos/cosine00/workouts_page/actions/workflows/run_data_sync.yml/dispatches`,
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${process.env.GITHUB_TOKEN}`,
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: 'master' }), // 触发哪个分支
    }
  );

  if (response.ok) {
    res.status(200).send('🚀 Action 已成功启动！');
  } else {
    res.status(500).send('触发失败，请检查 Token 权限');
  }
}