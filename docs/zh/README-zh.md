<p align="center"><img src="../assets/icon0.jpg" width="112" alt="减字图标"></p>

<h1 align="center">JIANZI · 减字</h1>
<p align="center"><img src="https://img.shields.io/badge/version-0.1.0-orange.svg" alt="版本 0.1.0"></p>
<p align="center"><strong>编译属于你自己的减字字体。</strong><br>数据库驱动的古琴减字谱字体编辑器与编译器。</p>

<p align="center">
  <a href="../../README.md">English</a>
</p>

**减字项目正在积极开发中，当前版本 0.1.0。**

## 简介

减字是一套数据库驱动的古琴减字谱字体流水线：自己设计风格化减字“部件”，在桌面 GUI 中“组装”出自己的自定义库，再将其编译为 TTF/OTF 字体。编译出的字体通过 OpenType GSUB 连字，把「大九挑七」这类输入编码直接渲染为减字字形，在任何支持 OpenType 的编辑器中都能使用。

项目的目标是一套完整的减字书写体验：让记谱像打字一样自然的字体、可整体替换部件轮廓而保留位置参数的风格包，以及规划中的减字谱输入法，都建立在当前的字体编译管线之上。**这些是路线图承诺；编辑—编译的字体流水线是已经就位的基础。**

## 安装

### 手动安装

克隆本仓库：

```bash
git clone https://github.com/ggbond-quantum/jianzi.git
cd jianzi
```

使用 [uv](https://docs.astral.sh/uv/) 创建虚拟环境并安装依赖：

```bash
uv sync
```

启动桌面应用：

```bash
uv run jianzi-gui
```


### 使用安装包

敬请期待……

## 工具

- [make_test_page.py](../../tools/make_test_page.py)：为编译出的减字字体生成测试页与样张，并执行发布校验——全量连字校验、简繁等价校验、单字 〇 前缀规则校验，以及 Windows 下的 GDI 可安装性校验。详细用法：

```bash
uv run python tools/make_test_page.py --help
```
