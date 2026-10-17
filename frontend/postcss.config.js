// Tailwind v4：构建入口从 `tailwindcss`（v3 PostCSS 插件）换成 `@tailwindcss/postcss`。
// v4 内置 Lightning CSS 处理厂商前缀，不再需要 autoprefixer；
// 主题配置从 tailwind.config.ts 迁移进 src/index.css 的 @theme（CSS-first）。
export default {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};
