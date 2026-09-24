// 只用于引导页和配置表单的接入信息；Jev 的请求协议仍由后端负责。
export const JEV_CHANNELS = [
  {
    id: 'typesafe',
    name: 'TypeSafe 官方',
    intro: '从 Jev 官方获取密钥，适合已经有 TypeSafe 账号的用户。',
    keyUrl: 'https://console.typesafe.ai/',
    keyLabel: '打开 TypeSafe 控制台',
    keyStep: '登录或注册后，在控制台找到 API Keys，创建并复制一把密钥。',
    baseUrl: 'https://api.typesafe.ai',
    model: 'jev-1.13.0',
  },
  {
    id: 'openrouter',
    name: 'OpenRouter',
    intro: '如果你已有 OpenRouter 账号，可以用它的密钥调用 Jev。',
    keyUrl: 'https://openrouter.ai/settings/keys',
    keyLabel: '打开 OpenRouter 密钥页面',
    keyStep: '登录或注册后创建 API Key，复制以 sk-or- 开头的密钥。',
    baseUrl: 'https://openrouter.ai/api/alpha/decisions',
    model: 'typesafe/jev-1.13',
  },
]
