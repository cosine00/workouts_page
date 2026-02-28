// pages/api/trigger.js
export default async function handler(req, res) {
  console.log('[Trigger API] 开始处理请求', {
    method: req.method,
    query: req.query,
    time: new Date().toISOString()
  });
  
  // 只允许 GET 请求（方便通过浏览器链接触发）
  if (req.method !== 'GET') {
    console.log('[错误] 请求方法不支持:', req.method);
    return res.status(405).json({
      success: false,
      error: '只支持 GET 方法',
      method: req.method
    });
  }
  
  try {
    // ========== 1. 验证环境变量 ==========
    console.log('[步骤1] 检查环境变量');
    
    const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
    const GITHUB_REPO = process.env.GITHUB_REPO;
    
    console.log('[环境变量状态]', {
      hasGithubToken: GITHUB_TOKEN ? '已设置 (长度: ' + GITHUB_TOKEN.length + ')' : '未设置',
      hasGithubRepo: GITHUB_REPO ? '已设置: ' + GITHUB_REPO : '未设置',
      nodeEnv: process.env.NODE_ENV || '未设置'
    });
    
    // 检查必要的环境变量
    if (!GITHUB_TOKEN) {
      console.error('[错误] GITHUB_TOKEN 未设置');
      return res.status(500).json({
        success: false,
        error: '配置错误',
        message: 'GITHUB_TOKEN 环境变量未设置',
        solution: '请在 Vercel 项目设置中添加 GITHUB_TOKEN 环境变量'
      });
    }
    
    if (!GITHUB_REPO) {
      console.error('[错误] GITHUB_REPO 未设置');
      return res.status(500).json({
        success: false,
        error: '配置错误',
        message: 'GITHUB_REPO 环境变量未设置',
        solution: '请在 Vercel 项目设置中添加 GITHUB_REPO 环境变量，格式为: 用户名/仓库名'
      });
    }
    
    // ========== 2. 解析仓库信息 ==========
    console.log('[步骤2] 解析仓库信息');
    
    const repoParts = GITHUB_REPO.split('/');
    if (repoParts.length !== 2 || !repoParts[0] || !repoParts[1]) {
      console.error('[错误] GITHUB_REPO 格式错误:', GITHUB_REPO);
      return res.status(500).json({
        success: false,
        error: '配置错误',
        message: 'GITHUB_REPO 格式错误',
        current: GITHUB_REPO,
        correctFormat: 'owner/repo (例如: octocat/hello-world)',
        note: '请确保不包含 https://github.com/ 前缀或 .git 后缀'
      });
    }
    
    const owner = repoParts[0].trim();
    const repo = repoParts[1].trim();
    
    console.log('[仓库信息]', { owner, repo });
    
    // ========== 3. API Token 验证 (可选) ==========
    console.log('[步骤3] API Token 验证');
    
    const API_TOKEN = process.env.API_TOKEN;
    if (API_TOKEN) {
      const { token } = req.query;
      console.log('[API Token 验证]', {
        provided: token ? '已提供 (长度: ' + token.length + ')' : '未提供',
        expected: '已设置环境变量'
      });
      
      if (!token || token !== API_TOKEN) {
        console.error('[错误] API Token 验证失败');
        return res.status(401).json({
          success: false,
          error: '未授权',
          message: '无效的 API Token',
          note: '请在 URL 中添加 ?token=你的API_TOKEN 参数',
          example: `https://${req.headers.host}/api/trigger?token=你的API_TOKEN`
        });
      }
      console.log('[API Token 验证] 通过');
    } else {
      console.log('[API Token 验证] 未设置，跳过验证');
    }
    
    // ========== 4. 获取工作流配置 ==========
    console.log('[步骤4] 获取工作流配置');
    
    // 允许通过查询参数指定工作流文件，默认使用 'deploy.yml'
    const workflowFile = req.query.workflow || 'run_data_sync.yml';
    const branch = req.query.branch || 'master';
    
    console.log('[工作流配置]', {
      workflowFile,
      branch,
      inputs: req.query.inputs || '无'
    });
    
    // ========== 5. 调用 GitHub API ==========
    console.log('[步骤5] 调用 GitHub API');
    
    const githubApiUrl = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflowFile}/dispatches`;
    console.log('[GitHub API URL]', githubApiUrl);
    
    // 准备请求体
    const requestBody = {
      ref: branch
    };
    
    // 如果有输入参数，尝试解析
    if (req.query.inputs) {
      try {
        requestBody.inputs = JSON.parse(req.query.inputs);
        console.log('[输入参数] 已解析:', requestBody.inputs);
      } catch (e) {
        console.warn('[输入参数] 解析失败，忽略:', e.message);
      }
    }
    
    console.log('[请求体]', JSON.stringify(requestBody, null, 2));
    
    // 发送请求到 GitHub API
    const startTime = Date.now();
    let response;
    
    try {
      response = await fetch(githubApiUrl, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${GITHUB_TOKEN}`,
          'Accept': 'application/vnd.github.v3+json',
          'Content-Type': 'application/json',
          'User-Agent': 'Vercel-GitHub-Action-Trigger/1.0'
        },
        body: JSON.stringify(requestBody)
      });
    } catch (fetchError) {
      console.error('[GitHub API 请求失败]', fetchError);
      return res.status(502).json({
        success: false,
        error: '网络错误',
        message: '无法连接到 GitHub API',
        details: fetchError.message
      });
    }
    
    const responseTime = Date.now() - startTime;
    console.log('[GitHub API 响应]', {
      status: response.status,
      statusText: response.statusText,
      responseTime: responseTime + 'ms'
    });
    
    // ========== 6. 处理响应 ==========
    console.log('[步骤6] 处理响应');
    
    const responseText = await response.text();
    let responseData;
    
    try {
      responseData = responseText ? JSON.parse(responseText) : {};
    } catch (e) {
      responseData = { rawText: responseText };
    }
    
    console.log('[响应内容]', responseData);
    
    if (response.ok) {
      console.log('[成功] GitHub Action 已触发');
      
      return res.status(200).json({
        success: true,
        message: 'GitHub Action 触发成功!',
        details: {
          repository: `${owner}/${repo}`,
          workflow: workflowFile,
          branch: branch,
          triggeredAt: new Date().toISOString(),
          apiResponseTime: responseTime + 'ms'
        },
        nextSteps: [
          '前往 GitHub 仓库查看 Action 执行状态',
          'Action 可能需要几分钟时间执行'
        ],
        links: {
          actionsPage: `https://github.com/${owner}/${repo}/actions`,
          repository: `https://github.com/${owner}/${repo}`
        }
      });
    } else {
      console.error('[错误] GitHub API 返回错误:', response.status, responseData);
      
      // 根据状态码提供具体的错误信息
      let errorMessage = 'GitHub API 错误';
      let suggestions = [];
      
      switch (response.status) {
        case 401:
          errorMessage = '身份验证失败';
          suggestions = [
            '检查 GITHUB_TOKEN 是否正确',
            '确保 Token 有足够的权限 (需要 repo 或 workflow 权限)',
            'Token 可能已过期，重新生成一个'
          ];
          break;
        case 403:
          errorMessage = '权限不足或被拒绝';
          suggestions = [
            'Token 权限不足，确保有 repo 权限',
            '检查是否达到 GitHub API 速率限制',
            '仓库可能设置为私有，需要相应权限'
          ];
          break;
        case 404:
          errorMessage = '未找到资源';
          suggestions = [
            `检查仓库 ${owner}/${repo} 是否存在`,
            `检查工作流文件 ${workflowFile} 是否存在`,
            '确保仓库名称拼写正确'
          ];
          break;
        case 422:
          errorMessage = '请求无效';
          suggestions = [
            '工作流可能未启用 workflow_dispatch 触发',
            '检查分支名称是否正确',
            '请求参数可能不符合工作流定义'
          ];
          break;
        default:
          suggestions = ['查看 GitHub API 响应获取更多信息'];
      }
      
      return res.status(response.status).json({
        success: false,
        error: errorMessage,
        details: {
          statusCode: response.status,
          statusText: response.statusText,
          githubMessage: responseData.message || '无详细错误信息',
          documentation: 'https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event'
        },
        suggestions: suggestions,
        troubleshooting: [
          '验证 GitHub Token 是否有效',
          `确认仓库 ${owner}/${repo} 存在且你有权限`,
          `确认工作流文件 .github/workflows/${workflowFile} 存在`,
          '在工作流文件中添加 workflow_dispatch 触发条件'
        ]
      });
    }
    
  } catch (error) {
    // ========== 7. 处理未预期的错误 ==========
    console.error('[未预期错误]', error);
    
    return res.status(500).json({
      success: false,
      error: '服务器内部错误',
      message: error.message,
      stack: process.env.NODE_ENV === 'development' ? error.stack : undefined,
      timestamp: new Date().toISOString(),
      commonCauses: [
        '环境变量配置错误',
        'GitHub Token 格式不正确',
        '网络连接问题',
        '代码逻辑错误'
      ],
      checkList: [
        '1. 确保在 Vercel 中设置了 GITHUB_TOKEN 和 GITHUB_REPO 环境变量',
        '2. 重新部署项目使环境变量生效',
        '3. 检查 GitHub Token 是否有 repo 和 workflow 权限',
        '4. 确认仓库路径格式为: 用户名/仓库名'
      ]
    });
  }
}
