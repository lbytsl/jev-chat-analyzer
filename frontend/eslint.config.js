// ESLint 扁平配置（eslint 9）。
//
// 定位是「抓真正的错」，不参与排版：所有格式规则都交给 prettier，最后用
// eslint-config-prettier 关掉与它冲突的规则。
//
// 刻意只开 vue 的 `essential` 而不是 `recommended`：recommended 里大量是属性换行、
// 缩进之类的风格规则，会和现有模板的写法（一行一个属性、2 空格）来回打架。
import js from '@eslint/js'
import prettier from 'eslint-config-prettier'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'

export default [
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**'] },

  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],
  prettier,

  {
    files: ['**/*.{js,mjs,vue}'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      // `catch (err) { /* 保留默认文案 */ }` 这种写法在本项目里是有意的（吞掉解析失败），
      // 默认的 caughtErrors: 'all' 会把它们全报成错。
      'no-unused-vars': ['error', { args: 'after-used', caughtErrors: 'none' }],
      // 组件名就是单文件组件文件名（ChatFlow / ReplyDock…），不需要多词约束。
      'vue/multi-word-component-names': 'off',
    },
  },

  {
    // 说话人识别的正则要跟后端 `app/domain/transcript.py` **逐字对应**，这两条是误报：
    // - 字符类里转义斜杠（`\d{4}[-\/]\d{1,2}`）：刻意与后端写法保持一致，改一处就得改两处；
    // - 正则里的全角空格（U+3000）：微信导出的时间行用的就是它，必须匹配。
    //   （这里只写说明，不把那个字符抄进注释——否则本文件自己就会被这条规则报错。）
    files: ['src/utils/transcript.js'],
    rules: { 'no-useless-escape': 'off', 'no-irregular-whitespace': 'off' },
  },
]
