#!/bin/bash
# 华泰证券 OpenClaw Skills 测试脚本

INSTALL_ROOT="$HOME/.solo/skills"
export HT_APIKEY="ht_BZVMWXVUH8KzRTcX4UaGQcRneAUndsSnj1KlTFIRS"

echo "========================================"
echo "华泰证券 OpenClaw Skills 测试"
echo "========================================"
echo ""

# 测试 query-indicator
echo "📊 测试 1/5: query-indicator"
python3 "$INSTALL_ROOT/query-indicator/query_indicator.py" queryIndicator --query "华泰证券最新价"
echo ""

# 测试 financial-analysis
echo "📈 测试 2/5: financial-analysis"
python3 "$INSTALL_ROOT/financial-analysis/financial_analysis.py" marketInsight --query "今天大盘怎么样"
echo ""

# 测试 select-stock
echo "🔍 测试 3/5: select-stock"
python3 "$INSTALL_ROOT/select-stock/select_stock.py" selectStock --query "科技板块上周涨幅前10的股票"
echo ""

# 测试 watchlist-management
echo "📋 测试 4/5: watchlist-management"
python3 "$INSTALL_ROOT/watchlist-management/watchlist_management.py" getWatchlist --query "查看我的自选股"
echo ""

# 测试 a-share-paper-trading
echo "💰 测试 5/5: a-share-paper-trading"
python3 "$INSTALL_ROOT/a-share-paper-trading/a_share_paper_trading.py" getAccountBalance
echo ""

echo "========================================"
echo "测试完成！"
echo "========================================"
