/**
 * rescue-map-tools：智能体的 map_view 工具（逻辑在 tools.ts）。放进预设的插件组里，随智能体会话加载；
 * 指令发给进程内共享的指令中心（getHub），由地图插件推给网页端。
 */

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

import type { Context } from '../adapter/dsh.ts'
import { defineTool, Schema } from '../adapter/dsh.ts'
import { getHub } from './hub.ts'
import { geometryNames } from './names.ts'
import type { RecordLike } from './record-label.ts'
import { ACTIONS, NOTES, runMapView } from './tools.ts'
import type { MapViewArgs } from './tools.ts'

export const name = 'rescue-map-tools'
export const inject = ['tools']

export interface Config {
  /** 地图包目录（geometry.json） */
  dataDir: string
  recordDir: string
}

export const Config: Schema<Config> = Schema.object({
  dataDir: Schema.string().required(),
  recordDir: Schema.string().required(),
})

const jsonOutput = {
  schema: { type: 'json' as const },
  render: (_args: unknown, value: unknown) => [{ type: 'text' as const, text: JSON.stringify(value, null, 2) }],
}

export function apply(ctx: Context, config: Config): void {
  const names = geometryNames(config.dataDir)
  const readRecord = async (id: string): Promise<RecordLike | null> => {
    try {
      return JSON.parse(await readFile(join(config.recordDir, `${id}.json`), 'utf8')) as RecordLike
    } catch {
      return null
    }
  }
  ;(ctx as any).tools.register(
    defineTool({
      name: 'map_view',
      description:
        '控制网页端的矿井通风图：定位（focus）、标记（mark）、取消标记（unmark）、清除你的标记（clear）、倾斜（view）、复位（reset），list 查地图上的名称。' +
        '用于向操作员说明“在哪里”：研判后把问题所在的巷道、工作面定位出来，重要的位置用 mark 标出。只是显示，不会执行任何处置。' +
        '名称必须是地图上有的，不确定先 action=list（可加 query）。标记要带依据：给 record_id（标签的等级、阶段、报警项数由系统从记录里取，你不要写数字），' +
        `或者只选一个 note（${NOTES.join('、')}），它只说“看这里”，不能自由写；对位置的判断（撤人路线、处置建议）不要放进标记，在回答里说明。` +
        '多个标记在同一轮里一起调用。预警等级、矿井报警、火区由系统自动标在图上，不用你重复标。',
      parameters: {
        action: { type: 'string', required: true, enum: [...ACTIONS], description: 'list 查名称；focus 定位；mark 标记；unmark 取消某处标记；clear 清除你所有的标记；view 倾斜；reset 视图复位' },
        name: { type: 'string', description: '地图上的名称（focus、mark、unmark 用），如“211016采面”；必须在地图上' },
        record_id: { type: 'string', description: 'mark 的依据：记录编号（RS-…、RP-…、EV-…），照抄工具给出的，不要自己编' },
        note: { type: 'string', enum: [...NOTES], description: 'mark 的备注，只能从这几个里选，不能自由写' },
        zoom: { type: 'number', description: 'focus 的放大倍数，缺省 4' },
        tilt: { type: 'number', description: 'view 的倾斜角度，0 到 60' },
        query: { type: 'string', description: 'list 的关键词，如“211016”“回风”' },
      },
      output: jsonOutput,
      execute: async (args: MapViewArgs) => runMapView(args, { hub: getHub(), names: names() ?? new Set<string>(), readRecord }) as never,
    }),
  )
}
