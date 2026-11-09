// 把浏览器端插件（client/src）打成 DSH 的懒加载 CJS 模块：lib/client.js。
// 格式见 DSH 自带客户端包：window.__ModuleLoader__.load({ id, factory: (require) => exports })，
// id 必须等于 package.json 的 name。React 等由平台共享表提供（external），three 打进包里。
import { build } from 'esbuild'
import { readFileSync } from 'node:fs'

const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
const watch = process.argv.includes('--check')

await build({
  entryPoints: [new URL('./src/index.ts', import.meta.url).pathname],
  outfile: new URL('../lib/client.js', import.meta.url).pathname,
  bundle: true,
  format: 'cjs',
  platform: 'browser',
  target: 'es2022',
  minify: !watch,
  sourcemap: false,
  legalComments: 'none',
  external: ['react', 'react/jsx-runtime', 'react-dom'],
  banner: {
    js: `window.__ModuleLoader__.load({id:${JSON.stringify(pkg.name)},factory:(require)=>{var module={exports:{}};var exports=module.exports;`,
  },
  footer: { js: 'return module.exports;}});' },
  logLevel: 'info',
})
