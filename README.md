# 160Grab

健康160全自动挂号脚本 — 基于 Playwright headless browser 自动化

## 功能

- Playwright headless 浏览器自动化挂号
- 支持定时刷号、抢号
- 图形验证码识别 (ddddocr)
- 多通道并发刷号
- Server酱 / Bark 通知推送

## 环境要求

- Python 3.11+
- uv (包管理)
- Chromium (Playwright 浏览器)

## 快速开始

```bash
# 安装依赖
uv sync

# 安装浏览器
uv run playwright install chromium

# 运行
uv run python main.py
```

## 项目结构

```
src/grab/
├── browser/        # Playwright 客户端
├── core/          # 核心调度逻辑
├── models/        # 数据模型
└── utils/         # 工具函数
```
