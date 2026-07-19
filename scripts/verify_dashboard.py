#!/usr/bin/env python
"""验证 canonical React + FastAPI 研究工作台，不认可旧 Streamlit 入口。"""

import sys
from pathlib import Path

# 将项目添加到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def check_imports():
    """检查所有必需的导入"""
    print("检查导入...")

    checks = [("pandas", "数据处理")]

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

    modules = [("aqsp.core.time", "时间工具")]

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

    modules = [("aqsp.web.data_provider", "研究数据提供器")]

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
        ("frontend/package.json", "React 前端配置"),
        ("backend/app.py", "FastAPI 服务入口"),
        ("scripts/start_vibe_research_service.sh", "canonical 服务启动脚本"),
        ("scripts/health_vibe_research.sh", "canonical 健康检查脚本"),
        ("deploy/nginx/vibe-research-mainline.conf", "canonical Nginx 入口"),
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
        ("frontend", "React 前端目录"),
        ("backend", "FastAPI 后端目录"),
        ("scripts", "脚本目录"),
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

    frontend_path = project_root / "frontend/src"
    backend_path = project_root / "backend/app.py"
    if not frontend_path.exists() or not backend_path.exists():
        print("  ✗ canonical frontend 或 backend 文件缺失")
        return False
    frontend_text = "\n".join(
        path.read_text(encoding="utf-8")
        for pattern in ("*.tsx", "*.ts")
        for path in frontend_path.rglob(pattern)
    )
    backend_text = backend_path.read_text(encoding="utf-8")
    checks = [
        ("当天结论", "正式研究模块"),
        ("消息证据", "消息研究模块"),
        ("候选研究", "候选研究模块"),
        ("讨论复核", "Agent 复核模块"),
        ("/api/aqsp/snapshot", "当前快照接口"),
    ]
    all_passed = True
    for needle, label in checks:
        haystack = frontend_text if needle != "/api/aqsp/snapshot" else backend_text
        if needle in haystack:
            print(f"  ✓ {needle:28} - {label}")
        else:
            print(f"  ✗ {needle:28} - {label}")
            all_passed = False
    return all_passed


def check_script_executable():
    """检查启动脚本权限"""
    print("\n检查脚本权限...")

    script = project_root / "scripts/start_vibe_research_service.sh"
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
    print("AQSP canonical 研究工作台验证")
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
        print("\n启动 canonical 服务:")
        print("  bash scripts/start_vibe_research_service.sh")
        return 0
    else:
        print("\n✗ 部分检查失败，请先解决上述问题。")
        print(
            "  旧 Streamlit 入口不属于 canonical 生产工作台。"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
