import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { describe, it } from 'node:test'

import { defaultPanels } from '../src/evidence/check.ts'

const SKILLS = fileURLToPath(new URL('../../skills', import.meta.url))

describe('check_mine 缺省检测的工作面', () => {
  it('取 watch.yaml 的 panels；RESCUE_WATCH_PANELS 覆盖（与监听器一致）', async () => {
    assert.deepEqual(await defaultPanels(SKILLS, {}), ['1201'])
    assert.deepEqual(await defaultPanels(SKILLS, { RESCUE_WATCH_PANELS: ' 211016, 1202 ,' }), ['211016', '1202'])
  })
})
