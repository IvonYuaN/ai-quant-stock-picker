# AQSP Repo Radar

目的：先记录宽视角参考系，不急着现在全部实现。当前已抓取并去重 **296** 个 GitHub 相关项目，供后续短/中/长线、A/H/美股、多 agent、组合管理扩展时使用。

当前建议优先吸收方向：
- `Portfolio Manager` 主裁决层：吸收多 agent 输出，做最终保留/剔除/降权。
- `Persona Registry` 人格层：短线、中线、长线三套人格并存，而不是只做短线角色。
- `Backtest + Walkforward` 强化：未来接更强的窗口化研究/参数冻结/跨市场验证。
- `Cross-market substrate`：为 A 股主链保留港股、美股数据和标的抽象扩展口。

## Agent / Persona / Committee

- [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | ⭐ 59692 | Python | An AI Hedge Fund Team
- [ValueCell-ai/valuecell](https://github.com/ValueCell-ai/valuecell) | ⭐ 10770 | Python | ValueCell is a community-driven, multi-agent platform for financial applications.
- [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | ⭐ 9476 | Python | "Vibe-Trading: Your Personal Trading Agent"
- [brokermr810/QuantDinger](https://github.com/brokermr810/QuantDinger) | ⭐ 7135 | Python | AI quantitative trading platform for crypto, stocks, and forex with backtesting, live trading, market data, and multi-agent research.vibe-trading ,trading-agents,ai-trader,ai-trading
- [hudson-and-thames/mlfinlab](https://github.com/hudson-and-thames/mlfinlab) | ⭐ 4788 | Python | MlFinLab helps portfolio managers and traders who want to leverage the power of machine learning by providing reproducible, interpretable, and easy to use tools.
- [olaxbt/ai-market-maker](https://github.com/olaxbt/ai-market-maker) | ⭐ 1370 | TypeScript | Agentic AI Hedge Fund OS (AIMM)
- [FinStep-AI/ContestTrade](https://github.com/FinStep-AI/ContestTrade) | ⭐ 647 | Python | A Multi-Agent Trading System Based on Internal Contest Mechanism
- [marketcalls/vectorbt-backtesting-skills](https://github.com/marketcalls/vectorbt-backtesting-skills) | ⭐ 149 | Python | Agentic coding skills for backtesting trading strategies using VectorBT. Supports Indian, US, and Crypto markets    with realistic transaction cost modeling, TA-Lib indicators, QuantStats tearsheets, and 12 ready-made strategy      templates.
- [AI-Brokers/AIBrokers](https://github.com/AI-Brokers/AIBrokers) | ⭐ 124 | Python | The first real-world AI hedge fund framework in crypto, fully open source!
- [FareedKhan-dev/multi-agent-trading-system](https://github.com/FareedKhan-dev/multi-agent-trading-system) | ⭐ 106 | Jupyter Notebook | Implementation of Deep Thinking Trading System
- [gael55x/LayeredMemoryTrader](https://github.com/gael55x/LayeredMemoryTrader) | ⭐ 32 | Python | LMT (LayeredMemoryTrader) is a multi-agent trading system using LLMs with human-style short/mid/long memory debates.
- [bcefghj/multi-agent-trading-system](https://github.com/bcefghj/multi-agent-trading-system) | ⭐ 31 | Python | 多Agent量化交易与投资决策系统 | 6-Agent并行+辩论+风控 | Python/Java/Go三语言实现 | 配套面试八股文+STAR法+简历模板 | 从零到面试全攻略
- [jmanhype/claude-code-plugin-marketplace](https://github.com/jmanhype/claude-code-plugin-marketplace) | ⭐ 27 | Python | Multi-agent trading, swarm intelligence, and GitHub automation plugins for Claude Code. 19 production-grade plugins built from 68+ specialized agents.
- [DanisHack/ai-hedge-fund](https://github.com/DanisHack/ai-hedge-fund) | ⭐ 25 | Python | AI-native hedge fund using multi-agent LLM system with real market data and paper trading.
- [renee-jia/alpha-agent](https://github.com/renee-jia/alpha-agent) | ⭐ 20 | Python | An AI-driven multi-agent trading platform for options trading and stock trends analysis. This project leverages advanced machine learning, real-time market data, and a modular multi-agent framework.

## Backtesting / Simulation

- [mementum/backtrader](https://github.com/mementum/backtrader) | ⭐ 21795 | Python | Python Backtesting library for trading strategies
- [quantopian/zipline](https://github.com/quantopian/zipline) | ⭐ 19837 | Python | Zipline, a Pythonic Algorithmic Trading Library
- [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | ⭐ 19640 | C# | Lean Algorithmic Trading Engine by QuantConnect (Python, C#)
- [kernc/backtesting.py](https://github.com/kernc/backtesting.py) | ⭐ 8457 | Python | 🔎 📈 🐍 💰  Backtest trading strategies in Python.
- [paperswithbacktest/awesome-systematic-trading](https://github.com/paperswithbacktest/awesome-systematic-trading) | ⭐ 8291 | Python | A curated list of awesome libraries, packages, strategies, books, blogs, tutorials for systematic trading.
- [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | ⭐ 7749 | Python | The backtesting engine that gives you an unfair advantage. Run thousands of trading ideas before others finish one.
- [brokermr810/QuantDinger](https://github.com/brokermr810/QuantDinger) | ⭐ 7135 | Python | AI quantitative trading platform for crypto, stocks, and forex with backtesting, live trading, market data, and multi-agent research.vibe-trading ,trading-agents,ai-trader,ai-trading
- [ricequant/rqalpha](https://github.com/ricequant/rqalpha) | ⭐ 6437 | Python | A extendable, replaceable Python algorithmic backtest && trading framework supporting multiple securities
- [Superalgos/Superalgos](https://github.com/Superalgos/Superalgos) | ⭐ 5498 | JavaScript | Free, open-source crypto trading bot, automated bitcoin / cryptocurrency trading software, algorithmic trading bots. Visually design your crypto trading bot, leveraging an integrated charting system, data-mining, backtesting, paper trading, and multi-server crypto bot deployments.
- [nkaz001/hftbacktest](https://github.com/nkaz001/hftbacktest) | ⭐ 4140 | Rust | Free, open source, a high frequency trading and market making backtesting and trading bot, which accounts for limit orders, queue positions, and latencies, utilizing full tick data for trades and order books(Level-2 and Level-3), with real-world crypto trading examples for Binance and Bybit
- [cuemacro/finmarketpy](https://github.com/cuemacro/finmarketpy) | ⭐ 3770 | Python | Python library for backtesting trading strategies & analyzing financial markets (formerly pythalesians)
- [jrothschild33/learn_backtrader](https://github.com/jrothschild33/learn_backtrader) | ⭐ 2165 | Python | BackTrader中文教程笔记（by：量化投资与机器学习），系统性介绍Bactrader的特性、策略构建、数据结构、回测交易等，彻底掌握量化神器的使用方法。章节：介绍篇、数据篇、指标篇、交易篇、策略篇、可视化篇……（持续更新中）
- [barter-rs/barter-rs](https://github.com/barter-rs/barter-rs) | ⭐ 2157 | Rust | Open-source Rust framework for building event-driven live-trading & backtesting systems
- [Yvictor/TradingGym](https://github.com/Yvictor/TradingGym) | ⭐ 1877 | Python | Trading and Backtesting environment for training reinforcement learning agent or simple rule base algo.
- [enzoampil/fastquant](https://github.com/enzoampil/fastquant) | ⭐ 1749 | Jupyter Notebook | fastquant — Backtest and optimize your ML trading strategies with only 3 lines of code!

## Portfolio / Risk / Allocation

- [ranaroussi/quantstats](https://github.com/ranaroussi/quantstats) | ⭐ 7200 | Python | Portfolio analytics for quants, written in Python
- [PyPortfolio/PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt) | ⭐ 5758 | Jupyter Notebook | Financial portfolio optimisation in python, including classical efficient frontier, Black-Litterman, Hierarchical Risk Parity
- [hudson-and-thames/mlfinlab](https://github.com/hudson-and-thames/mlfinlab) | ⭐ 4788 | Python | MlFinLab helps portfolio managers and traders who want to leverage the power of machine learning by providing reproducible, interpretable, and easy to use tools.
- [dcajasn/Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib) | ⭐ 4243 | C++ | Portfolio Optimization in Python
- [jmfernandes/robin_stocks](https://github.com/jmfernandes/robin_stocks) | ⭐ 2080 | Python | This is a library to use with Robinhood Financial App. It currently supports trading crypto-currencies, options, and stocks. In addition, it can be used to get real time ticker information, assess the performance of your portfolio, and can also get tax documents, total dividends paid, and more. More info at
- [skfolio/skfolio](https://github.com/skfolio/skfolio) | ⭐ 2010 | Python | Python library for portfolio optimization built on top of scikit-learn
- [cvxgrp/cvxportfolio](https://github.com/cvxgrp/cvxportfolio) | ⭐ 1219 | Python | Portfolio optimization and back-testing.
- [jankrepl/deepdow](https://github.com/jankrepl/deepdow) | ⭐ 1140 | Python | Portfolio optimization with deep learning.
- [santoshlite/EigenLedger](https://github.com/santoshlite/EigenLedger) | ⭐ 1060 | Python | An Open Source Portfolio Backtesting Engine for Everyone | 面向所有人的开源投资组合回测引擎
- [NVIDIA-AI-Blueprints/cuFOLIO](https://github.com/NVIDIA-AI-Blueprints/cuFOLIO) | ⭐ 388 | Jupyter Notebook | cuFOLIO is a GPU-accelerated portfolio optimization toolkit for building, backtesting, and scaling modern investment workflows with NVIDIA cuOpt and CUDA-X Data Science.
- [VivekPa/OptimalPortfolio](https://github.com/VivekPa/OptimalPortfolio) | ⭐ 370 | Python | An open source library for portfolio optimisation
- [czielinski/portfolioopt](https://github.com/czielinski/portfolioopt) | ⭐ 313 | Python | Financial Portfolio Optimization Routines in Python
- [fortitudo-tech/fortitudo.tech](https://github.com/fortitudo-tech/fortitudo.tech) | ⭐ 298 | Python | Entropy Pooling views and stress testing combined with Conditional Value-at-Risk (CVaR) portfolio optimization in Python.
- [areed1192/portfolio-optimization](https://github.com/areed1192/portfolio-optimization) | ⭐ 107 | Python | A python application, that demonstrates optimizing a portfolio using machine learning.
- [jialuechen/deepfolio](https://github.com/jialuechen/deepfolio) | ⭐ 101 | Python | Quadratic Programming based Python Package for Portfolio Optimization

## Data / Market Access

- [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | ⭐ 68412 | Python | Financial data platform for analysts, quants and AI agents.
- [akfamily/akshare](https://github.com/akfamily/akshare) | ⭐ 19977 | Python | AKShare is an elegant and simple financial data interface library for Python, built for human beings! 开源财经数据接口库
- [waditu/tushare](https://github.com/waditu/tushare) | ⭐ 15074 | Python | TuShare is a utility for crawling historical data of China stocks
- [JerBouma/FinanceDatabase](https://github.com/JerBouma/FinanceDatabase) | ⭐ 7761 | Python | This is a database of 300.000+ symbols containing Equities, ETFs, Funds, Indices, Currencies, Cryptocurrencies and Money Markets.
- [brokermr810/QuantDinger](https://github.com/brokermr810/QuantDinger) | ⭐ 7135 | Python | AI quantitative trading platform for crypto, stocks, and forex with backtesting, live trading, market data, and multi-agent research.vibe-trading ,trading-agents,ai-trader,ai-trading
- [shashankvemuri/Finance](https://github.com/shashankvemuri/Finance) | ⭐ 3902 | Python | 150+ quantitative finance Python programs to help you gather, manipulate, and analyze stock market data
- [TreborNamor/TradingView-Machine-Learning-GUI](https://github.com/TreborNamor/TradingView-Machine-Learning-GUI) | ⭐ 950 | Python | HyperView is a terminal-first TradingView strategy lab for downloading market data, backtesting Python strategies with Pine-like behavior, and optimizing SL/TP parameters.
- [ArturSepp/QuantInvestStrats](https://github.com/ArturSepp/QuantInvestStrats) | ⭐ 569 | Python | Quantitative Investment Strategies (QIS) package implements Python analytics for visualisation of financial data, performance reporting, analysis of quantitative strategies.
- [panpanpandas/ultrafinance](https://github.com/panpanpandas/ultrafinance) | ⭐ 437 | Python | Python project for real-time financial data collection, analyzing && backtesting trading strategies
- [chenwr727/stock-backtrader-web-app](https://github.com/chenwr727/stock-backtrader-web-app) | ⭐ 254 | Python | Stock Backtrader Web App 是一个基于 Python 的项目，旨在简化股票回测和分析的过程。通过集成四个强大的库——Streamlit、AkShare、Backtrader 和 Pyecharts，本应用为用户提供了一个综合性的工具集，支持股票数据获取、回测模拟和结果可视化，且所有功能都在一个直观的 Web 界面内完成。
- [OnePunchMonk/AgentQuant](https://github.com/OnePunchMonk/AgentQuant) | ⭐ 108 | Python | Autonomous quantitative trading research platform that transforms stock lists into fully backtested strategies using AI agents, real market data, and mathematical formulations, all without requiring any coding.
- [zwldarren/akshare-one](https://github.com/zwldarren/akshare-one) | ⭐ 68 | Python | Standardized interface for Chinese financial market data, built on AKShare with unified data formats and simplified APIs
- [jiuhuang-asset/jh_quant](https://github.com/jiuhuang-asset/jh_quant) | ⭐ 62 | Python | 开源量化交易平台 | 兼容 akshare/tushare 调用风格 | 回测引擎 · 因子模型 · 实盘交易 · 可视化仪表盘
- [DanisHack/ai-hedge-fund](https://github.com/DanisHack/ai-hedge-fund) | ⭐ 25 | Python | AI-native hedge fund using multi-agent LLM system with real market data and paper trading.
- [renee-jia/alpha-agent](https://github.com/renee-jia/alpha-agent) | ⭐ 20 | Python | An AI-driven multi-agent trading platform for options trading and stock trends analysis. This project leverages advanced machine learning, real-time market data, and a modular multi-agent framework.

## CN / A-share

- [vnpy/vnpy](https://github.com/vnpy/vnpy) | ⭐ 41238 | Python | 基于Python的开源量化交易平台开发框架
- [akfamily/akshare](https://github.com/akfamily/akshare) | ⭐ 19977 | Python | AKShare is an elegant and simple financial data interface library for Python, built for human beings! 开源财经数据接口库
- [waditu/tushare](https://github.com/waditu/tushare) | ⭐ 15074 | Python | TuShare is a utility for crawling historical data of China stocks
- [veighna-global/vnpy_binance](https://github.com/veighna-global/vnpy_binance) | ⭐ 414 | Python | Binance trading gateway for VeighNa Evo
- [veighna-global/vnpy_evo](https://github.com/veighna-global/vnpy_evo) | ⭐ 379 | Python | The core module for using VeighNa (vnpy) quant trading platform on the crypto market.
- [touhoufan2024/qlibAssistant](https://github.com/touhoufan2024/qlibAssistant) | ⭐ 375 | Python | qlib助手, 每日自动预测a股 👇
- [sphinx-quant/sphinx-quant](https://github.com/sphinx-quant/sphinx-quant) | ⭐ 360 | Python | 一个基于vnpy，支持多账户，多策略，实盘交易，数据分析，分布式在线回测，风险管理，多交易节点的量化交易系统；支持CTP期货，股票，期权，数字货币等金融产品
- [chenwr727/stock-backtrader-web-app](https://github.com/chenwr727/stock-backtrader-web-app) | ⭐ 254 | Python | Stock Backtrader Web App 是一个基于 Python 的项目，旨在简化股票回测和分析的过程。通过集成四个强大的库——Streamlit、AkShare、Backtrader 和 Pyecharts，本应用为用户提供了一个综合性的工具集，支持股票数据获取、回测模拟和结果可视化，且所有功能都在一个直观的 Web 界面内完成。
- [zhaoxusun/stock-quant](https://github.com/zhaoxusun/stock-quant) | ⭐ 198 | Python | K线数据获取-量化回测-数据分析-策略选股（A股、港股、美股）
- [ling-0729/KHunter](https://github.com/ling-0729/KHunter) | ⭐ 188 | Python | KHunter 是一套开箱即用的A股量化交易系统，集数据管理、策略选股、择时交易、风险控制、回测验证于一体，为个人投资者提供从数据到交易的全流程量化解决方案。
- [veighna-global/vnpy_okx](https://github.com/veighna-global/vnpy_okx) | ⭐ 170 | Python | OKX trading gateway for VeighNa Evo
- [ZhuLinsen/alphasift](https://github.com/ZhuLinsen/alphasift) | ⭐ 116 | Python | AI-native A-share stock screening engine with full-market discovery, LLM ranking, risk-aware scoring, and auditable evaluation. AI选股
- [seasonstar/atmquant](https://github.com/seasonstar/atmquant) | ⭐ 94 | Python | atmquant由公众号“堂主的ATMQuant"开发，是基于vnpy框架的AI量化交易平台，专注于AI量化投资、指标信号可视化与策略研发和回测，有完整教学和实战案例，适合量化交易初学者、金融从业者、程序员、投资爱好者
- [chinobing/QuantInvest](https://github.com/chinobing/QuantInvest) | ⭐ 82 | Jupyter Notebook | cnvar.cn及个人微信公众号【QuantInvest】里面提及的编程代码, 对股票各种研究和折腾分析A股市场的各种现象和投资机会，涉及编程、股票模型、分析研究、杂谈等，代码是python，以jupyter notebook展示。
- [zwldarren/akshare-one](https://github.com/zwldarren/akshare-one) | ⭐ 68 | Python | Standardized interface for Chinese financial market data, built on AKShare with unified data formats and simplified APIs

## HK / US Expansion

- [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | ⭐ 68412 | Python | Financial data platform for analysts, quants and AI agents.
- [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) | ⭐ 39893 | Python | LLM驱动的 A/H/美股智能分析：多数据源行情 + 实时新闻 + LLM决策仪表盘 + 多渠道推送，零成本定时运行，纯白嫖. LLM-powered stock analysis system for A/H/US markets.
- [quantopian/zipline](https://github.com/quantopian/zipline) | ⭐ 19837 | Python | Zipline, a Pythonic Algorithmic Trading Library
- [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | ⭐ 9476 | Python | "Vibe-Trading: Your Personal Trading Agent"
- [JerBouma/FinanceDatabase](https://github.com/JerBouma/FinanceDatabase) | ⭐ 7761 | Python | This is a database of 300.000+ symbols containing Equities, ETFs, Funds, Indices, Currencies, Cryptocurrencies and Money Markets.
- [quantopian/alphalens](https://github.com/quantopian/alphalens) | ⭐ 4292 | Jupyter Notebook | Performance analysis of predictive (alpha) stock factors
- [josephchenhk/qtrader](https://github.com/josephchenhk/qtrader) | ⭐ 549 | Python | A Light Event-Driven Algorithmic Trading Engine
- [PlaceNL2026/best-of-algorithmic-trading](https://github.com/PlaceNL2026/best-of-algorithmic-trading) | ⭐ 269 | TypeScript | algorithmic trading curated list quant finance trading bots backtesting technical analysis crypto open-source freqtrade hummingbot fintech Python TypeScript resources MCP quantopian-style rankings
- [jeffreyrdcs/stock-vcpscreener](https://github.com/jeffreyrdcs/stock-vcpscreener) | ⭐ 79 | Python | A python stock screener that calculates market breadth  and selects US stocks on a daily basis
- [alexjansenhome/GEM](https://github.com/alexjansenhome/GEM) | ⭐ 60 | Python | Python implementation of Antonacci's GEM ("Global Equities Momentum") strategy
- [aconstandinou/pairs-trading-equities](https://github.com/aconstandinou/pairs-trading-equities) | ⭐ 24 | Python | 
- [aspromatis/Backtesting-RSI-Algo](https://github.com/aspromatis/Backtesting-RSI-Algo) | ⭐ 21 | Python | Backtesting an RSI Trading Algorithm with Quantopian Zipline and Pyfolio Python Libraries
- [pixelwhiz/tasty-schwab-trader-BE](https://github.com/pixelwhiz/tasty-schwab-trader-BE) | ⭐ 13 | Python | Multi-strategy algorithmic trading platform designed for institutional-grade automated execution across futures, equities, and options markets. Built with Python and Flask, this system provides real-time market analysis, multi-broker integration, and comprehensive risk management capabilities.
- [rlancaster243/Quant_Agent](https://github.com/rlancaster243/Quant_Agent) | ⭐ 12 | Python | 🚀 AI-powered multi-agent trading analysis system with Streamlit UI. Features technical indicators, pattern recognition, trend analysis, and LLM-driven decision synthesis using Groq API. Built with Python, OpenBB, and matplotlib for comprehensive market insights.
- [cy-Yin/TradingAgents-CN-lite](https://github.com/cy-Yin/TradingAgents-CN-lite) | ⭐ 7 | Python | Lightweight multi-agent trading analysis framework with A-share, HK, and US market support

## ML / Research

- [microsoft/qlib](https://github.com/microsoft/qlib) | ⭐ 43945 | Python | Qlib is an AI-oriented Quant investment platform that aims to use AI tech to empower Quant Research, from exploring ideas to implementing productions. Qlib supports diverse ML modeling paradigms, including supervised learning, market dynamics modeling, and RL, and is now equipped with https://github.com/microsoft/RD-Agent to automate R&D process.
- [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | ⭐ 15314 | Jupyter Notebook | FinRL®:  Financial Reinforcement Learning. 🔥
- [huseinzol05/Stock-Prediction-Models](https://github.com/huseinzol05/Stock-Prediction-Models) | ⭐ 9369 | Jupyter Notebook | Gathers machine learning and deep learning models for Stock forecasting including trading bots and simulations
- [firmai/financial-machine-learning](https://github.com/firmai/financial-machine-learning) | ⭐ 8578 | Python | A curated list of practical financial machine learning tools and applications.
- [hudson-and-thames/mlfinlab](https://github.com/hudson-and-thames/mlfinlab) | ⭐ 4788 | Python | MlFinLab helps portfolio managers and traders who want to leverage the power of machine learning by providing reproducible, interpretable, and easy to use tools.
- [edtechre/pybroker](https://github.com/edtechre/pybroker) | ⭐ 3336 | Python | Algorithmic Trading in Python with Machine Learning
- [TradeMaster-NTU/TradeMaster](https://github.com/TradeMaster-NTU/TradeMaster) | ⭐ 2758 | Jupyter Notebook | TradeMaster is an open-source platform for quantitative trading empowered by reinforcement learning :fire: :zap: :rainbow:
- [achillesrasquinha/bulbea](https://github.com/achillesrasquinha/bulbea) | ⭐ 2284 | Python | :boar: :bear: Deep Learning based Python Library for Stock Market Prediction and Modelling
- [Yvictor/TradingGym](https://github.com/Yvictor/TradingGym) | ⭐ 1877 | Python | Trading and Backtesting environment for training reinforcement learning agent or simple rule base algo.
- [firmai/machine-learning-asset-management](https://github.com/firmai/machine-learning-asset-management) | ⭐ 1740 | Jupyter Notebook | Machine Learning in Asset Management (by @firmai)
- [ryanfrigo/kalshi-ai-trading-bot](https://github.com/ryanfrigo/kalshi-ai-trading-bot) | ⭐ 424 | Python | A toolkit for building AI-automated trading strategies on Kalshi prediction markets.
- [aulekator/Polymarket-BTC-15-Minute-Trading-Bot](https://github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot) | ⭐ 415 | Python | A production-grade algorithmic trading bot for Polymarket's 15-minute BTC price prediction markets. Built with a 7-phase architecture combining multiple signal sources, professional risk management, and self-learning capabilities.
- [microsoft/qlib-server](https://github.com/microsoft/qlib-server) | ⭐ 376 | Python | Qlib-Server is the data server system for Qlib. It enable Qlib to run in online mode. Under online mode, the data will be deployed as a shared data service. The data and their cache will be shared by all the clients. The data retrieval performance is expected to be improved due to a higher rate of cache hits. It will consume less disk space, too.
- [touhoufan2024/qlibAssistant](https://github.com/touhoufan2024/qlibAssistant) | ⭐ 375 | Python | qlib助手, 每日自动预测a股 👇
- [qusong0627/QuantMind](https://github.com/qusong0627/QuantMind) | ⭐ 305 | Python | QuantMind 开源版 是一款面向个人量化研究者的本地化金融量化交易平台，基于微软 Qlib 量化框架构建，提供从模型训练，回测，推理，实盘交易的完整研究闭环。 平台深度集成 LightGBM 等主流机器学习模型，支持 146 维量化因子训练与推理，用户可快速构建 Alpha 策略并在历史数据上验证效果。核心功能涵盖智能策略生成、模型训练、回测中心、QuantBot 助手及多模型管理，全部功能无使用限制。 开源版采用本地单机部署，通过 docker compose 一键启动，无需依赖云服务，数据与模型完全本地化，保障研究隐私。适合个人开发者、学术研究者及小团队进行量化策略原型验证与二次开发，是进入金融量化领域的理想起点。

## Screening / Selection

- [myhhub/stock](https://github.com/myhhub/stock) | ⭐ 12830 | Python | stock股票.获取股票数据,计算股票指标,筹码分布,识别股票形态,综合选股,选股策略,股票验证回测,股票自动交易,支持PC及移动设备。
- [atilaahmettaner/tradingview-mcp](https://github.com/atilaahmettaner/tradingview-mcp) | ⭐ 2960 | Python | Real-time crypto & stock screening, advanced technical indicators, Bollinger Bands intelligence, candlestick patterns + native Claude Desktop integration. Multi-exchange (Binance, KuCoin, Bybit+). Open-source AI trading infrastructure.
- [pranjal-joshi/Screeni-py](https://github.com/pranjal-joshi/Screeni-py) | ⭐ 686 | Python | A Python-based stock screener to find stocks with potential breakout probability from NSE India.
- [pkjmesra/PKScreener](https://github.com/pkjmesra/PKScreener) | ⭐ 352 | Python | A Python-based stock screener for NSE, India. PKScreener is an advanced free stock screener to find potential breakout stocks from NSE and show its possible breakout values. It also helps to find the stocks which are consolidating and may breakout, or the particular chart patterns that you're looking specifically to make your decisions.
- [hackingthemarkets/stockscreener](https://github.com/hackingthemarkets/stockscreener) | ⭐ 327 | Python | Build a Stock Screener using FastAPI (Python)
- [deshwalmahesh/NSE-Stock-Scanner](https://github.com/deshwalmahesh/NSE-Stock-Scanner) | ⭐ 317 | Jupyter Notebook | National Stock Exchange (NSE), India based Stock screener program. Supports Live Data, Swing / Momentum Trading, Intraday Trading, Connect to online brokers as Zerodha Kite, Risk Management, Emotion Control, Screening, Strategies, Backtesting, Automatic Stock Downloading after closing, live free day trading data and much more
- [zhaoxusun/stock-quant](https://github.com/zhaoxusun/stock-quant) | ⭐ 198 | Python | K线数据获取-量化回测-数据分析-策略选股（A股、港股、美股）
- [ling-0729/KHunter](https://github.com/ling-0729/KHunter) | ⭐ 188 | Python | KHunter 是一套开箱即用的A股量化交易系统，集数据管理、策略选股、择时交易、风险控制、回测验证于一体，为个人投资者提供从数据到交易的全流程量化解决方案。
- [lseffer/stock_screener](https://github.com/lseffer/stock_screener) | ⭐ 140 | Python | Picking stocks through various screening methods. Focus on Northern Europe.
- [ZhuLinsen/alphasift](https://github.com/ZhuLinsen/alphasift) | ⭐ 116 | Python | AI-native A-share stock screening engine with full-market discovery, LLM ranking, risk-aware scoring, and auditable evaluation. AI选股
- [devfinwiz/Stock_Screeners_Raw](https://github.com/devfinwiz/Stock_Screeners_Raw) | ⭐ 85 | Python | This repository enables traders/investors to spot undervalued stocks automatically in the market efficiently to help them maximise their profits.
- [jeffreyrdcs/stock-vcpscreener](https://github.com/jeffreyrdcs/stock-vcpscreener) | ⭐ 79 | Python | A python stock screener that calculates market breadth  and selects US stocks on a daily basis
- [Lucas-Kohorst/Python-Stock](https://github.com/Lucas-Kohorst/Python-Stock) | ⭐ 69 | Python | Predicting stock prices from Yahoo stock screener using scikit-learn and sending the predicitons via smtplib to a phone number.
- [terzim/StockScreener](https://github.com/terzim/StockScreener) | ⭐ 62 | Python | A handy tool for screening stocks based on certain criteria from several markets around the world. The list can then be delivered to your email address (one-off or regularly via crontab).
- [starboi-63/growth-stock-screener](https://github.com/starboi-63/growth-stock-screener) | ⭐ 37 | Python | An automated stock screening system which isolates top companies based on time-tested growth criteria.

## Next Absorption Candidates

- `virattt/ai-hedge-fund`: 人格投资大师层 + Portfolio Manager 裁决层。
- `microsoft/qlib`: 中长线研究基座、ML 因子研究、自动化研究流程。
- `akfamily/akquant`: 高性能回测/研究执行内核，适合和 AQSP 编排层拼接。
- `OpenBB-finance/OpenBB`: 跨市场数据和研究工具抽象。
- `backtrader` / `vectorbt` / `Lean`: 回测和组合验证思路对照组。
