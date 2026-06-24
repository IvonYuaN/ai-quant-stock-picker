#!/usr/bin/env python
"""仪表盘验证脚本 - 检查研究工作台依赖、模块与关键能力入口"""

import sys
from pathlib import Path

# 将项目添加到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def check_imports():
    """检查所有必需的导入"""
    print("检查导入...")

    checks = [
        ("streamlit", "Streamlit Web框架"),
        ("pandas", "数据处理"),
    ]

    for module_name, description in checks:
        try:
            __import__(module_name)
            print(f"  ✓ {module_name:20} - {description}")
        except ImportError as e:
            print(f"  ✗ {module_name:20} - {description}")
            print(f"    错误: {e}")
            return False

    return True


def check_project_modules():
    """检查项目模块"""
    print("\n检查项目模块...")

    modules = [
        ("aqsp.portfolio.position_tracker", "持仓追踪器"),
        ("aqsp.risk.stop_loss", "止损管理器"),
        ("aqsp.audit.trade_logger", "交易日志"),
        ("aqsp.ledger.base", "账本系统"),
        ("aqsp.core.time", "时间工具"),
    ]

    for module_name, description in modules:
        try:
            __import__(module_name)
            print(f"  ✓ {module_name:30} - {description}")
        except ImportError as e:
            print(f"  ✗ {module_name:30} - {description}")
            print(f"    错误: {e}")
            return False

    return True


def check_web_modules():
    """检查web模块"""
    print("\n检查web模块...")

    modules = [
        ("aqsp.web.config", "仪表盘配置"),
        ("aqsp.web.data_provider", "数据提供器"),
        ("aqsp.web.dashboard", "仪表盘主程序"),
    ]

    for module_name, description in modules:
        try:
            __import__(module_name)
            print(f"  ✓ {module_name:30} - {description}")
        except ImportError as e:
            print(f"  ✗ {module_name:30} - {description}")
            print(f"    错误: {e}")
            return False

    return True


def check_files():
    """检查必需的文件"""
    print("\n检查文件结构...")

    files = [
        ("src/aqsp/web/__init__.py", "Web模块初始化"),
        ("src/aqsp/web/dashboard.py", "仪表盘程序"),
        ("src/aqsp/web/data_provider.py", "数据提供器"),
        ("scripts/start_dashboard.sh", "启动脚本"),
        (".streamlit/config.toml", "Streamlit配置"),
    ]

    for file_path, description in files:
        full_path = project_root / file_path
        if full_path.exists():
            size = full_path.stat().st_size
            print(f"  ✓ {file_path:35} - {description} ({size} bytes)")
        else:
            print(f"  ✗ {file_path:35} - {description} (未找到)")
            return False

    return True


def check_directories():
    """检查必需的目录"""
    print("\n检查目录结构...")

    dirs = [
        ("src/aqsp/web", "Web模块目录"),
        ("scripts", "脚本目录"),
        (".streamlit", "Streamlit配置目录"),
        ("docs", "文档目录"),
    ]

    for dir_path, description in dirs:
        full_path = project_root / dir_path
        if full_path.exists() and full_path.is_dir():
            print(f"  ✓ {dir_path:35} - {description}")
        else:
            print(f"  ✗ {dir_path:35} - {description} (未找到)")
            return False

    return True


def check_dashboard_capabilities():
    """检查当前研究工作台关键能力入口是否存在。"""
    print("\n检查研究工作台能力入口...")

    dashboard_path = project_root / "src/aqsp/web/dashboard.py"
    provider_path = project_root / "src/aqsp/web/data_provider.py"
    if not dashboard_path.exists() or not provider_path.exists():
        print("  ✗ dashboard 或 data_provider 文件缺失")
        return False

    dashboard_text = dashboard_path.read_text(encoding="utf-8")
    provider_text = provider_path.read_text(encoding="utf-8")
    checks = [
        ("决策首页", "首页工作区入口"),
        ("焦点候选", "首页统一候选焦点"),
        ("候选复盘", "按标的聚焦研究视图"),
        ("虚拟盘跟踪", "按标的聚焦纸面验证视图"),
        ("归档回看", "按日期回看归档"),
        ("same_day_candidate_journey", "provider 候选路径接口"),
    ]
    all_passed = True
    for needle, label in checks:
        haystack = (
            dashboard_text if needle != "same_day_candidate_journey" else provider_text
        )
        if needle in haystack:
            print(f"  ✓ {needle:28} - {label}")
        else:
            print(f"  ✗ {needle:28} - {label}")
            all_passed = False
    return all_passed


def check_script_executable():
    """检查启动脚本权限"""
    print("\n检查脚本权限...")

    script = project_root / "scripts/start_dashboard.sh"
    if script.exists():
        import os

        if os.access(script, os.X_OK):
            print(f"  ✓ {script.name:35} - 可执行")
        else:
            print(f"  ⚠ {script.name:35} - 需要执行权限")
            print(f"    运行: chmod +x {script}")
    else:
        print(f"  ✗ {script.name:35} - 未找到")
        return False

    return True


def main():
    """运行所有检查"""
    print("=" * 60)
    print("A股量化选股 - Streamlit仪表盘验证")
    print("=" * 60)

    checks = [
        ("导入依赖", check_imports),
        ("项目模块", check_project_modules),
        ("Web模块", check_web_modules),
        ("文件结构", check_files),
        ("目录结构", check_directories),
        ("能力入口", check_dashboard_capabilities),
        ("脚本权限", check_script_executable),
    ]

    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ {name} 检查出错: {e}")
            results.append((name, False))

    print("\n" + "=" * 60)
    print("检查结果总结")
    print("=" * 60)

    all_passed = True
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status:8} {name}")
        if not result:
            all_passed = False

    print("=" * 60)

    if all_passed:
        print("\n✓ 所有检查通过！研究工作台可以启动。")
        print("\n启动仪表盘:")
        print("  bash scripts/start_dashboard.sh")
        print("\n或直接运行:")
        print("  streamlit run src/aqsp/web/dashboard.py")
        return 0
    else:
        print("\n✗ 部分检查失败，请先解决上述问题。")
        print(
            "  如果只有 streamlit 缺失，则属于本地运行环境问题，不代表工作台代码本身有误。"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
