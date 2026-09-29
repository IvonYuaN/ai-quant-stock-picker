// 复盘笔记页面 —— 对历史信号进行标签、笔记和评分
//
// 功能：
// 1. 展示历史信号列表（从 AQSP 快照获取）
// 2. 每个信号可以添加/编辑复盘笔记
// 3. 评分系统（1-5星）
// 4. 标签选择/自定义输入
// 5. 笔记富文本编辑（Markdown）
// 6. 按日期、标签、评分过滤

import { useEffect, useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { BookOpen, Edit, Star, Tags, Trash2, Filter, X } from "lucide-react";
import { api, type ReviewRecord, type SignalRecord } from "@/lib/api";
import { Badge, EmptyState, StatePanel } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

type Review = ReviewRecord;
type Signal = SignalRecord;

export function ReviewPage() {
  const [reviews, setReviews] = useState<Review[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [allTags, setAllTags] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 过滤器状态
  const [filterSymbol, setFilterSymbol] = useState("");
  const [filterDate, setFilterDate] = useState("");
  const [filterTags, setFilterTags] = useState<string[]>([]);
  const [filterMinRating, setFilterMinRating] = useState<number | null>(null);

  // 编辑状态
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editRating, setEditRating] = useState(5);
  const [editTags, setEditTags] = useState<string[]>([]);
  const [editNotes, setEditNotes] = useState("");
  const [editSignalId, setEditSignalId] = useState("");
  const [editDate, setEditDate] = useState("");
  const [editSymbol, setEditSymbol] = useState("");
  const [newTagInput, setNewTagInput] = useState("");

  // 加载数据
  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      // 并行加载复盘记录、历史信号和标签
      const [reviewRows, signalsPayload, tagList] = await Promise.all([
        api.reviews(),
        api.signals(100),
        api.reviewTags(),
      ]);

      setReviews(reviewRows);
      setSignals(signalsPayload.signals);
      setAllTags(tagList);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };

  // 过滤后的复盘记录
  const filteredReviews = useMemo(() => {
    return reviews.filter((r) => {
      if (filterSymbol && !r.symbol.includes(filterSymbol)) return false;
      if (filterDate && r.date !== filterDate) return false;
      if (filterTags.length > 0 && !filterTags.some((tag) => r.tags.includes(tag))) return false;
      if (filterMinRating !== null && r.rating < filterMinRating) return false;
      return true;
    });
  }, [reviews, filterSymbol, filterDate, filterTags, filterMinRating]);

  // 创建复盘记录
  const handleCreate = async (signal: Signal) => {
    setEditingId("new");
    setEditSignalId(signal.id);
    setEditDate(signal.signal_date);
    setEditSymbol(signal.symbol);
    setEditRating(signal.win === true ? 5 : signal.win === false ? 2 : 3);
    setEditTags([]);
    setEditNotes("");
  };

  // 编辑复盘记录
  const handleEdit = (review: Review) => {
    setEditingId(review.id);
    setEditSignalId(review.signal_id);
    setEditDate(review.date);
    setEditSymbol(review.symbol);
    setEditRating(review.rating);
    setEditTags([...review.tags]);
    setEditNotes(review.notes);
  };

  // 保存复盘记录
  const handleSave = async () => {
    try {
      if (editingId === "new" || editingId == null) {
        // 创建新记录（editingId 为 null 表示还没进入编辑态，也按创建处理）
        await api.createReview({
          signal_id: editSignalId,
          date: editDate,
          symbol: editSymbol,
          rating: editRating,
          tags: editTags,
          notes: editNotes,
        });
      } else {
        // 更新现有记录
        await api.updateReview(editingId, {
          rating: editRating,
          tags: editTags,
          notes: editNotes,
        });
      }

      setEditingId(null);
      await loadData();
    } catch (err) {
      alert(err instanceof Error ? err.message : "保存失败");
    }
  };

  // 删除复盘记录
  const handleDelete = async (id: string) => {
    if (!confirm("确认删除这条复盘记录？")) return;

    try {
      await api.deleteReview(id);
      await loadData();
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败");
    }
  };

  // 添加标签
  const handleAddTag = () => {
    const tag = newTagInput.trim();
    if (tag && !editTags.includes(tag)) {
      setEditTags([...editTags, tag]);
      setNewTagInput("");
    }
  };

  // 移除标签
  const handleRemoveTag = (tag: string) => {
    setEditTags(editTags.filter((t) => t !== tag));
  };

  // 切换过滤标签
  const toggleFilterTag = (tag: string) => {
    setFilterTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  };

  if (loading) {
    return <StatePanel>正在加载复盘笔记…</StatePanel>;
  }

  if (error) {
    return <StatePanel tone="warn">加载失败：{error}</StatePanel>;
  }

  return (
    <div className="aq-page">
      <header className="aq-page-header">
        <div className="aq-page-header-main">
          <h1>
            <BookOpen className="aq-inline-icon" aria-hidden="true" />
            复盘笔记
          </h1>
          <p className="aq-page-desc">对历史信号进行标签、笔记和评分</p>
        </div>
      </header>

      {/* 过滤器 */}
      <section className="aq-section">
        <div className="aq-section-head">
          <h2>
            <Filter className="aq-inline-icon" aria-hidden="true" />
            过滤条件
          </h2>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">股票代码</label>
            <input
              type="text"
              className="aq-input"
              placeholder="000001"
              value={filterSymbol}
              onChange={(e) => setFilterSymbol(e.target.value)}
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">日期</label>
            <input
              type="date"
              className="aq-input"
              value={filterDate}
              onChange={(e) => setFilterDate(e.target.value)}
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">最低评分</label>
            <select
              className="aq-input"
              value={filterMinRating ?? ""}
              onChange={(e) =>
                setFilterMinRating(e.target.value ? Number(e.target.value) : null)
              }
            >
              <option value="">全部</option>
              <option value="1">1星+</option>
              <option value="2">2星+</option>
              <option value="3">3星+</option>
              <option value="4">4星+</option>
              <option value="5">5星</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">标签</label>
            <div className="flex flex-wrap gap-1">
              {allTags.slice(0, 10).map((tag) => (
                <button
                  key={tag}
                  className={cn(
                    "px-2 py-1 text-xs rounded",
                    filterTags.includes(tag)
                      ? "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200"
                      : "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300"
                  )}
                  onClick={() => toggleFilterTag(tag)}
                >
                  {tag}
                </button>
              ))}
            </div>
          </div>
        </div>

        {(filterSymbol || filterDate || filterTags.length > 0 || filterMinRating !== null) && (
          <div className="mt-4">
            <button
              className="text-sm text-blue-600 dark:text-blue-400 hover:underline"
              onClick={() => {
                setFilterSymbol("");
                setFilterDate("");
                setFilterTags([]);
                setFilterMinRating(null);
              }}
            >
              清除全部过滤条件
            </button>
          </div>
        )}
      </section>

      {/* 复盘记录列表 */}
      <section className="aq-section">
        <div className="aq-section-head">
          <h2>复盘记录</h2>
          <span className="aq-section-count">{filteredReviews.length} 条</span>
        </div>

        {filteredReviews.length === 0 ? (
          <EmptyState
            icon={BookOpen}
            title="暂无复盘记录"
            detail="从下方历史信号中选择一个开始复盘"
          />
        ) : (
          <div className="space-y-4">
            {filteredReviews.map((review) => (
              <div key={review.id} className="aq-card">
                {editingId === review.id ? (
                  <ReviewEditor
                    rating={editRating}
                    tags={editTags}
                    notes={editNotes}
                    newTagInput={newTagInput}
                    allTags={allTags}
                    onRatingChange={setEditRating}
                    onNotesChange={setEditNotes}
                    onNewTagInputChange={setNewTagInput}
                    onAddTag={handleAddTag}
                    onRemoveTag={handleRemoveTag}
                    onSave={handleSave}
                    onCancel={() => setEditingId(null)}
                  />
                ) : (
                  <ReviewCard
                    review={review}
                    onEdit={() => handleEdit(review)}
                    onDelete={() => handleDelete(review.id)}
                  />
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 历史信号列表 */}
      <section className="aq-section">
        <div className="aq-section-head">
          <h2>历史信号</h2>
          <span className="aq-section-count">{signals.length} 条</span>
        </div>

        {signals.length === 0 ? (
          <EmptyState
            icon={BookOpen}
            title="暂无历史信号"
            detail="运行 AQSP 生成信号后可在此复盘"
          />
        ) : (
          <div className="aq-table-wrap">
            <table className="aq-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>代码</th>
                  <th>名称</th>
                  <th>评级</th>
                  <th className="aq-num">得分</th>
                  <th>策略</th>
                  <th>结果</th>
                  <th className="aq-num">收益率</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {signals.slice(0, 50).map((signal) => {
                  const hasReview = reviews.some((r) => r.signal_id === signal.id);
                  return (
                    <tr key={signal.id}>
                      <td>{signal.signal_date}</td>
                      <td>
                        <Link to={`/stock/${signal.symbol}`} className="aq-link">
                          {signal.symbol}
                        </Link>
                      </td>
                      <td>{signal.name}</td>
                      <td>
                        <Badge tone={signal.rating === "strong_buy_candidate" ? "ok" : "neutral"}>
                          {signal.rating}
                        </Badge>
                      </td>
                      <td className="aq-num">{signal.score}</td>
                      <td>{signal.strategies.join(", ")}</td>
                      <td>
                        {signal.win === true ? (
                          <Badge tone="ok">盈利</Badge>
                        ) : signal.win === false ? (
                          <Badge tone="warn">亏损</Badge>
                        ) : signal.status === "not_executable" ? (
                          <Badge tone="neutral">不可成交</Badge>
                        ) : (
                          <Badge tone="neutral">未验证</Badge>
                        )}
                      </td>
                      <td className={cn("aq-num", signal.return_pct && signal.return_pct > 0 ? "text-green-600" : "text-red-600")}>
                        {signal.return_pct != null ? `${signal.return_pct.toFixed(2)}%` : "—"}
                      </td>
                      <td>
                        {hasReview ? (
                          <span className="text-xs text-gray-500">已复盘</span>
                        ) : (
                          <button
                            className="text-xs text-blue-600 hover:underline"
                            onClick={() => handleCreate(signal)}
                          >
                            添加复盘
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* 编辑器弹窗 */}
      {editingId === "new" && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white dark:bg-gray-900 rounded-lg max-w-2xl w-full max-h-[90vh] overflow-y-auto p-6">
            <h3 className="text-lg font-semibold mb-4">
              创建复盘笔记 - {editSymbol} ({editDate})
            </h3>
            <ReviewEditor
              rating={editRating}
              tags={editTags}
              notes={editNotes}
              newTagInput={newTagInput}
              allTags={allTags}
              onRatingChange={setEditRating}
              onNotesChange={setEditNotes}
              onNewTagInputChange={setNewTagInput}
              onAddTag={handleAddTag}
              onRemoveTag={handleRemoveTag}
              onSave={handleSave}
              onCancel={() => setEditingId(null)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// 复盘卡片组件
function ReviewCard({
  review,
  onEdit,
  onDelete,
}: {
  review: Review;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div>
      <div className="flex items-start justify-between mb-2">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link to={`/stock/${review.symbol}`} className="font-semibold text-lg aq-link">
              {review.symbol}
            </Link>
            <span className="text-sm text-gray-500">{review.date}</span>
            <StarRating rating={review.rating} readonly />
          </div>
          {review.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-2">
              {review.tags.map((tag) => (
                <span
                  key={tag}
                  className="px-2 py-0.5 text-xs rounded bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200"
                >
                  <Tags className="inline w-3 h-3 mr-1" />
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="flex gap-2">
          <button
            className="p-2 hover:bg-gray-100 dark:hover:bg-gray-800 rounded"
            onClick={onEdit}
            title="编辑"
          >
            <Edit className="w-4 h-4" />
          </button>
          <button
            className="p-2 hover:bg-red-100 dark:hover:bg-red-900 rounded text-red-600"
            onClick={onDelete}
            title="删除"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>
      {review.notes && (
        <div className="prose prose-sm dark:prose-invert max-w-none">
          <pre className="whitespace-pre-wrap text-sm">{review.notes}</pre>
        </div>
      )}
      <div className="mt-2 text-xs text-gray-500">
        更新于 {new Date(review.updated_at).toLocaleString("zh-CN")}
      </div>
    </div>
  );
}

// 评分组件
function StarRating({
  rating,
  readonly = false,
  onChange,
}: {
  rating: number;
  readonly?: boolean;
  onChange?: (rating: number) => void;
}) {
  return (
    <div className="flex gap-1">
      {[1, 2, 3, 4, 5].map((star) => (
        <button
          key={star}
          type="button"
          disabled={readonly}
          className={cn(
            readonly ? "cursor-default" : "cursor-pointer hover:scale-110 transition-transform",
            "focus:outline-none"
          )}
          onClick={() => !readonly && onChange?.(star)}
        >
          <Star
            className={cn(
              "w-5 h-5",
              star <= rating ? "fill-yellow-400 text-yellow-400" : "text-gray-300"
            )}
          />
        </button>
      ))}
    </div>
  );
}

// 编辑器组件
function ReviewEditor({
  rating,
  tags,
  notes,
  newTagInput,
  allTags,
  onRatingChange,
  onNotesChange,
  onNewTagInputChange,
  onAddTag,
  onRemoveTag,
  onSave,
  onCancel,
}: {
  rating: number;
  tags: string[];
  notes: string;
  newTagInput: string;
  allTags: string[];
  onRatingChange: (rating: number) => void;
  onNotesChange: (notes: string) => void;
  onNewTagInputChange: (input: string) => void;
  onAddTag: () => void;
  onRemoveTag: (tag: string) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium mb-1">评分</label>
        <StarRating rating={rating} onChange={onRatingChange} />
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">标签</label>
        <div className="flex flex-wrap gap-1 mb-2">
          {tags.map((tag) => (
            <span
              key={tag}
              className="px-2 py-1 text-sm rounded bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200 flex items-center gap-1"
            >
              {tag}
              <button
                type="button"
                onClick={() => onRemoveTag(tag)}
                className="hover:text-red-600"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            className="aq-input flex-1"
            placeholder="添加标签"
            value={newTagInput}
            onChange={(e) => onNewTagInputChange(e.target.value)}
            onKeyPress={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                onAddTag();
              }
            }}
          />
          <button type="button" className="aq-btn-secondary" onClick={onAddTag}>
            添加
          </button>
        </div>
        {allTags.length > 0 && (
          <div className="mt-2">
            <p className="text-xs text-gray-500 mb-1">常用标签：</p>
            <div className="flex flex-wrap gap-1">
              {allTags.slice(0, 10).map((tag) => (
                <button
                  key={tag}
                  type="button"
                  className="px-2 py-0.5 text-xs rounded bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700"
                  onClick={() => {
                    if (!tags.includes(tag)) {
                      onRemoveTag(""); // trigger state update
                      onNewTagInputChange(tag);
                      onAddTag();
                    }
                  }}
                >
                  {tag}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">复盘笔记（支持 Markdown）</label>
        <textarea
          className="aq-input min-h-[200px] font-mono text-sm"
          placeholder="## 复盘总结&#10;&#10;### 优点&#10;- ...&#10;&#10;### 不足&#10;- ..."
          value={notes}
          onChange={(e) => onNotesChange(e.target.value)}
        />
      </div>

      <div className="flex gap-2 justify-end">
        <button type="button" className="aq-btn-secondary" onClick={onCancel}>
          取消
        </button>
        <button type="button" className="aq-btn-primary" onClick={onSave}>
          保存
        </button>
      </div>
    </div>
  );
}
