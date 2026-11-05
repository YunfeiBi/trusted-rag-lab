function sourcePath(file) {
  return `/documents/${encodeURIComponent(file.document_id)}/source?version_id=${encodeURIComponent(file.version_id)}`
}

export async function downloadFile(api, file) {
  const blob = await api(sourcePath(file), {}, 'blob')
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = file.filename
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 30_000)
}

export function openSource(api, file) {
  const tab = window.open('', '_blank')
  if (!tab) throw new Error('浏览器拦截了原文页面，请允许本站打开新标签页。')
  tab.opener = null
  const doc = tab.document
  doc.documentElement.lang = 'zh-CN'
  doc.title = file.filename
  doc.body.style.cssText = 'margin:24px;font:15px/1.7 system-ui;color:#233343'
  const heading = doc.createElement('h1')
  heading.textContent = file.filename
  const content = doc.createElement('main')
  content.textContent = '正在验证权限并加载原文…'
  doc.body.replaceChildren(heading, content)

  async function load() {
    const blob = await api(sourcePath(file), {}, 'blob')
    if (tab.closed) return
    const suffix = file.filename.split('.').pop().toLowerCase()
    const url = URL.createObjectURL(blob)
    tab.addEventListener('pagehide', () => URL.revokeObjectURL(url), { once:true })
    const download = doc.createElement('a')
    download.href = url
    download.download = file.filename
    download.textContent = '下载原文件'
    heading.after(download)
    if (suffix === 'pdf') {
      const frame = doc.createElement('iframe')
      frame.title = 'PDF 原文'
      frame.style.cssText = 'display:block;width:100%;height:78vh;border:0;margin-top:16px'
      frame.src = url + (file.page ? `#page=${file.page}` : '')
      content.replaceChildren(frame)
      return
    }
    const preview = doc.createElement('pre')
    preview.style.cssText = 'white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:20px'
    if (['md','txt','csv'].includes(suffix)) {
      const text = await blob.slice(0, 750_000).text()
      const lines = text.split(/\r?\n/)
      if (file.line_start && file.line_start <= lines.length) {
        const first = file.line_start - 1
        const last = file.line_end || file.line_start
        const mark = doc.createElement('mark')
        mark.textContent = lines.slice(first,last).join('\n')
        preview.append(lines.slice(0,first).join('\n'), first ? '\n' : '', mark)
        if (last < lines.length) preview.append('\n', lines.slice(last).join('\n'))
        content.replaceChildren(preview)
        mark.scrollIntoView({block:'center'})
      } else {
        preview.textContent = text
        content.replaceChildren(preview)
      }
      if (blob.size > 750_000) preview.append('\n（预览已截断，完整内容可下载。）')
      return
    }
    const note = doc.createElement('p')
    note.textContent = '以下为提取的文字，原始排版请下载后查看。'
    content.replaceChildren(note, preview)
    if (file.status && file.status !== 'ready') {
      preview.textContent = '该版本尚无可用的文字预览。'
      return
    }
    const offset = Math.max(0, (file.ordinal || 0) - 4)
    const path = `/documents/${encodeURIComponent(file.document_id)}/chunks?version_id=${encodeURIComponent(file.version_id)}&limit=100&offset=${offset}`
    const page = await api(path)
    for (const item of page.items) {
      const block = doc.createElement(item.id === file.chunk_id ? 'mark' : 'span')
      block.textContent = [item.section, item.text].filter(Boolean).join('\n')
      preview.append(block, '\n\n')
    }
    if (page.total > page.items.length) preview.append('（当前仅展示部分片段，完整内容可下载。）')
    preview.querySelector('mark')?.scrollIntoView({block:'center'})
  }

  load().catch(error => {
    if (!tab.closed) content.textContent = error.message
  })
}
