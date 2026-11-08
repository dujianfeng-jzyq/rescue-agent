/**
 * 报告交付成 Word 文档（.docx）：把技能模板渲染出的 Markdown（记录里的 content，仍是事实来源）转成 Word。
 * 模板只用到一小部分 Markdown：标题（#）、无序列表（- ）、有序列表（1. ）、段落、**加粗**、图片行。
 * 图片（风险图是 SVG，Word 不支持）不嵌入，改成一行文字说明，图文件仍按原样单独交付。
 */
import { AlignmentType, Document, HeadingLevel, Packer, Paragraph, TextRun } from 'docx'

const HEADINGS = [HeadingLevel.HEADING_1, HeadingLevel.HEADING_2, HeadingLevel.HEADING_3, HeadingLevel.HEADING_4]

/** 一行文字里的 **加粗** 拆成多个 run */
function runs(text: string, base: { bold?: boolean; size?: number } = {}): TextRun[] {
  return text
    .split(/(\*\*[^*]+\*\*)/)
    .filter((s) => s !== '')
    .map((s) => (s.startsWith('**') && s.endsWith('**') && s.length > 4 ? new TextRun({ text: s.slice(2, -2), ...base, bold: true }) : new TextRun({ text: s, ...base })))
}

export function markdownBlocks(markdown: string): Paragraph[] {
  const out: Paragraph[] = []
  let para: string[] = []
  const flush = () => {
    if (para.length) out.push(new Paragraph({ children: runs(para.join('')), spacing: { after: 120 } }))
    para = []
  }
  for (const raw of markdown.replace(/\r\n/g, '\n').split('\n')) {
    const line = raw.trimEnd()
    let m: RegExpMatchArray | null
    if (line.trim() === '') {
      flush()
    } else if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      flush()
      out.push(new Paragraph({ heading: HEADINGS[m[1]!.length - 1], children: runs(m[2]!), spacing: { before: 200, after: 120 } }))
    } else if ((m = line.match(/^!\[([^\]]*)\]\([^)]*\)\s*$/))) {
      flush()
      out.push(new Paragraph({ children: runs(`（图：${m[1] || '见单独交付的图文件'}，另见随报告交付的图文件）`), alignment: AlignmentType.LEFT, spacing: { after: 120 } }))
    } else if ((m = line.match(/^[-*]\s+(.*)$/))) {
      flush()
      out.push(new Paragraph({ children: runs(m[1]!), bullet: { level: 0 }, spacing: { after: 60 } }))
    } else if ((m = line.match(/^(\d+)[.、]\s+(.*)$/))) {
      flush()
      out.push(new Paragraph({ children: runs(`${m[1]}. ${m[2]}`), indent: { left: 360 }, spacing: { after: 60 } }))
    } else {
      para.push(line) // 相邻的普通行并成一段（与 Markdown 渲染一致）
    }
  }
  flush()
  return out
}

export async function markdownToDocx(markdown: string, title: string): Promise<Buffer> {
  const doc = new Document({
    title,
    creator: '煤矿应急救援智能体',
    styles: { default: { document: { run: { font: 'Microsoft YaHei', size: 22 } } } },
    sections: [{ children: markdownBlocks(markdown) }],
  })
  return Packer.toBuffer(doc)
}
