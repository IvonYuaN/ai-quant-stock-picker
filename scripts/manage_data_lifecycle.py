#!/usr/bin/env python3
"""数据生命周期管理工具 - 管理辩论结果等数据的存储和清理"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path


def get_file_info(path: Path) -> dict:
    """获取文件统计信息"""
    if not path.exists():
        return {"exists": False, "size": 0, "lines": 0}
    
    content = path.read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip()]
    
    return {
        "exists": True,
        "size": len(content),
        "lines": len(lines),
        "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def analyze_debate_file(path: Path) -> dict:
    """分析辩论结果文件"""
    if not path.exists():
        return {"error": "文件不存在"}
    
    data = path.read_text(encoding="utf-8")
    lines = [line for line in data.splitlines() if line.strip()]
    
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    
    date_counts = {}
    symbol_counts = {}
    all_dates = []
    
    for record in records:
        symbol = record.get("symbol", "未知")
        debate_date = record.get("debate_date", "未知")
        
        date_counts[debate_date] = date_counts.get(debate_date, 0) + 1
        symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
        all_dates.append(debate_date)
    
    valid_dates = [d for d in all_dates if d != "未知"]
    oldest_date = min(valid_dates) if valid_dates else "N/A"
    newest_date = max(valid_dates) if valid_dates else "N/A"
    
    if valid_dates:
        try:
            days_oldest = (datetime.now() - datetime.strptime(oldest_date, "%Y-%m-%d")).days
            days_newest = (datetime.now() - datetime.strptime(newest_date, "%Y-%m-%d")).days
        except ValueError:
            days_oldest = days_newest = 0
    else:
        days_oldest = days_newest = 0
    
    return {
        "total_records": len(records),
        "date_counts": dict(sorted(date_counts.items())),
        "symbol_counts": dict(sorted(symbol_counts.items())),
        "oldest_date": oldest_date,
        "newest_date": newest_date,
        "days_oldest": days_oldest,
        "days_newest": days_newest,
        "today": datetime.now().strftime("%Y-%m-%d"),
    }


def clean_old_debates(path: Path, keep_days: int = 30, dry_run: bool = False) -> dict:
    """清理过期的辩论结果"""
    if not path.exists():
        return {"error": "文件不存在"}
    
    data = path.read_text(encoding="utf-8")
    lines = [line for line in data.splitlines() if line.strip()]
    
    cutoff_date = (datetime.now() - timedelta(days=keep_days)).date().isoformat()
    
    kept_records = []
    deleted_records = []
    
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            debate_date = record.get("debate_date", "")
            
            if debate_date >= cutoff_date:
                kept_records.append(record)
            else:
                deleted_records.append({
                    "symbol": record.get("symbol"),
                    "date": debate_date,
                    "debate_id": record.get("debate_id"),
                })
        except json.JSONDecodeError:
            continue
    
    if dry_run:
        return {
            "dry_run": True,
            "cutoff_date": cutoff_date,
            "keep_days": keep_days,
            "would_keep": len(kept_records),
            "would_delete": len(deleted_records),
            "deleted_samples": deleted_records[:5],
        }
    
    with open(path, "w", encoding="utf-8") as f:
        for record in kept_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    return {
        "cutoff_date": cutoff_date,
        "keep_days": keep_days,
        "kept": len(kept_records),
        "deleted": len(deleted_records),
        "deleted_samples": deleted_records[:5],
    }


def main():
    parser = argparse.ArgumentParser(description="数据生命周期管理工具")
    parser.add_argument("--file", default="data/debate_results.jsonl", help="数据文件路径")
    parser.add_argument("--analyze", action="store_true", help="分析数据文件")
    parser.add_argument("--clean", action="store_true", help="清理过期数据")
    parser.add_argument("--keep-days", type=int, default=30, help="保留天数（默认30天）")
    parser.add_argument("--dry-run", action="store_true", help="预览清理效果，不实际删除")
    args = parser.parse_args()
    
    path = Path(args.file)
    
    print("\n📊 数据生命周期管理工具")
    print("=" * 60)
    
    info = get_file_info(path)
    print("\n📁 文件信息:")
    print(f"   路径: {path}")
    print(f"   存在: {'✅ 是' if info['exists'] else '❌ 否'}")
    if info['exists']:
        print(f"   大小: {info['size']:,} 字节 ({info['size'] / 1024:.2f} KB)")
        print(f"   记录数: {info['lines']} 条")
        print(f"   修改时间: {info['modified']}")
    
    if args.analyze or args.clean:
        print("\n📈 数据分析:")
        analysis = analyze_debate_file(path)
        if "error" in analysis:
            print(f"   ❌ {analysis['error']}")
        else:
            print(f"   总记录数: {analysis['total_records']} 条")
            print(f"   股票数: {len(analysis['symbol_counts'])} 只")
            print(f"   日期范围: {analysis['oldest_date']} 至 {analysis['newest_date']}")
            print(f"   最老记录: {analysis['days_oldest']} 天前")
            print(f"   最新记录: {analysis['days_newest']} 天前")
            
            print("\n   📅 按日期统计:")
            for date, count in list(analysis['date_counts'].items())[-10:]:
                print(f"      {date}: {count} 条")
            
            print("\n   📈 按股票统计:")
            for symbol, count in list(analysis['symbol_counts'].items())[:10]:
                print(f"      {symbol}: {count} 条")
    
    if args.clean:
        print("\n🧹 清理过期数据:")
        print(f"   保留策略: 最近 {args.keep_days} 天")
        
        result = clean_old_debates(path, args.keep_days, args.dry_run)
        
        if "error" in result:
            print(f"   ❌ {result['error']}")
        elif result.get("dry_run"):
            print("   🔍 预览模式（不实际删除）")
            print(f"   将保留: {result['would_keep']} 条")
            print(f"   将删除: {result['would_delete']} 条")
            if result['deleted_samples']:
                print("   删除样例:")
                for sample in result['deleted_samples']:
                    print(f"      - {sample['symbol']} ({sample['date']})")
        else:
            print("   ✅ 清理完成")
            print(f"   保留: {result['kept']} 条")
            print(f"   删除: {result['deleted']} 条")
            if result['deleted_samples']:
                print("   删除样例:")
                for sample in result['deleted_samples']:
                    print(f"      - {sample['symbol']} ({sample['date']})")
    
    print("\n" + "=" * 60)
    print()


if __name__ == "__main__":
    main()
