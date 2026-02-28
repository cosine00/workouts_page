export default async function handler(req, res) {
  // 可在此添加简单的身份验证，如查询参数验证
  const { token } = req.query;
  if (token !== process.env.API_TOKEN) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
  const [owner, repo] = process.env.GITHUB_REPO.split('/');
  const workflow_id = 'run_data_sync.yml'; // 你的工作流文件名

  try {
    const response = await fetch(
      `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow_id}/dispatches`,
      {
        method: 'POST',
        headers: {
          'Authorization': `token ${GITHUB_TOKEN}`,
          'Accept': 'application/vnd.github.v3+json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ref: 'master', // 触发分支
          // 可传递输入参数（如果工作流定义了 inputs）
        }),
      }
    );

    if (response.ok) {
      res.status(200).json({ message: '🚀 Action 已成功启动！ });
    } else {
      const error = await response.text();
      res.status(response.status).json({ error });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}
