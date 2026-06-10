interface Column<T> {
  key: string;
  title: string;
  render?: (row: T, index: number) => any;
}

interface TableProps<T> {
  columns: Column<T>[];
  data: T[];
  rowKey?: string;
  // TODO: 排序与分页实现计划（预留，当节点/规则数量超过 50+ 时启用）
  // 阶段1 — 客户端排序：添加 onSort?(key, order) 回调 + 排序指示器 UI
  //   - Column 接口增加 sortable?: boolean 和 sortOrder?: 'asc' | 'desc'
  //   - Table 内部维护 sortKey/sortOrder 状态，对 data 做客户端排序
  // 阶段2 — 客户端分页：添加 pageSize + currentPage + onPageChange
  //   - TableProps 增加 pageSize?: number（默认 25）、currentPage?: number
  //   - 渲染分页控件（上一页/下一页/页码），仅展示当前页数据
  // 阶段3 — 服务端分页（可选）：当数据量极大时，由后端分页返回
  //   - TableProps 增加 fetcher?: (page, size, sort) => Promise<{data, total}>
  //   - 替换本地 data 为 fetcher 返回值
  emptyText?: string;
}

export function Table<T extends Record<string, any>>({ columns, data, rowKey = 'id', emptyText = 'No data' }: TableProps<T>) {
  if (data.length === 0) {
    return <div class="text-center py-8 text-gray-500 dark:text-gray-400">{emptyText}</div>;
  }
  return (
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-gray-200 dark:border-gray-700">
            {columns.map(col => (
              <th key={col.key} class="px-4 py-3 text-left font-medium text-gray-500 dark:text-gray-400">
                {col.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr key={row[rowKey] ?? i} class="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700">
              {columns.map(col => (
                <td key={col.key} class="px-4 py-3">
                  {col.render ? col.render(row, i) : row[col.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
