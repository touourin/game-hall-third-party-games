/**
 * The browser half of the cutaway-office-tower geometry parity contract.
 *
 * `codex_v0/tower_shell_contract.py` builds the identical structure from the
 * offline QA renderer's own geometry. Both halves are compared against the
 * committed fixture at `checks/fixtures/tower-shell-geometry.json`, so neither
 * test suite has to reach into the other language's runtime; a one-sided edit
 * to the geometry shows up as a failure in the *other* suite.
 *
 * Run `.venv/bin/python -m codex_v0.tower_shell_contract regenerate` to rebuild
 * the fixture after an intentional geometry change. That command runs both
 * halves and refuses to write when they disagree.
 */

import { readFile } from 'node:fs/promises'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { towerShellGeometry, windowBandPolygon } from '../web/scene.mjs'

export const CONTRACT_SCHEMA_VERSION = 1
export const CONTRACT_PACK_ID = 'core-v2'
export const FIXTURE_URL = new URL('./fixtures/tower-shell-geometry.json', import.meta.url)

const LAYOUTS_URL = new URL('../assets/world-layouts.json', import.meta.url)
const PACK_SPEC_URL = new URL('../assets/core-v2-pack.spec.json', import.meta.url)

/**
 * Local scene space is an integer pixel lattice.
 *
 * This is load-bearing, not cosmetic: the browser snaps polygon vertices
 * *before* the camera transform while the Pillow renderer rounds *after* it.
 * While every local coordinate is an integer the two can only disagree by the
 * fractional part the camera zoom introduces — at most half a device pixel, and
 * exactly zero at integer zoom. A fractional local coordinate would break that
 * bound, so the contract refuses to record one.
 */
function exactInt(value, label) {
  const number = Number(value)
  if (!Number.isInteger(number)) {
    throw new RangeError(`${label} 必须是局部场景空间中的整数，实际是 ${value}`)
  }
  return number
}

function point(value, label) {
  return { x: exactInt(value.x, `${label}.x`), y: exactInt(value.y, `${label}.y`) }
}

function points(values, label) {
  return values.map((value, index) => point(value, `${label}[${index}]`))
}

/** First, middle and last band: the polygon is affine in depth, so three pin it. */
function sampleBandIndices(count) {
  if (count <= 0) return []
  return [...new Set([0, Math.floor(count / 2), count - 1])].sort((left, right) => left - right)
}

function faceContract(face, shell) {
  const height = shell.windowBandPitch - 3
  const bands = face.windowBands.map((depth, index) => exactInt(depth, `${face.id}.windowBands[${index}]`))
  return {
    topEdge: points(face.topEdge, `${face.id}.topEdge`),
    facade: points(face.facade, `${face.id}.facade`),
    slab: points(face.slab, `${face.id}.slab`),
    ambientOcclusion: points(face.ambientOcclusion, `${face.id}.ambientOcclusion`),
    mullions: face.mullions.map((mullion, index) => ({
      top: point(mullion.top, `${face.id}.mullions[${index}].top`),
      bottom: point(mullion.bottom, `${face.id}.mullions[${index}].bottom`),
    })),
    windowBands: bands,
    bounds: {
      left: exactInt(face.bounds.left, `${face.id}.bounds.left`),
      top: exactInt(face.bounds.top, `${face.id}.bounds.top`),
      right: exactInt(face.bounds.right, `${face.id}.bounds.right`),
      bottom: exactInt(face.bounds.bottom, `${face.id}.bounds.bottom`),
    },
    sampleBandPolygons: sampleBandIndices(bands.length).map((index) => ({
      depth: bands[index],
      height,
      points: points(
        windowBandPolygon(face, bands[index], height),
        `${face.id}.sampleBandPolygons[${index}]`,
      ),
    })),
  }
}

/** Every core-v2 world, smallest first, so the fixture tracks real product maps. */
export function contractLayouts(layoutsDocument) {
  return layoutsDocument.layouts
    .filter((layout) => layout.requiredPackId === CONTRACT_PACK_ID)
    .map((layout) => ({
      id: layout.id,
      columns: layout.columns,
      rows: layout.rows,
      origin: { x: layout.origin.x, y: layout.origin.y },
    }))
    .sort((left, right) => (left.id < right.id ? -1 : left.id > right.id ? 1 : 0))
}

export function buildTowerShellContract(layoutsDocument, packSpec) {
  const shell = packSpec.sceneShell
  if (!shell) throw new Error(`${CONTRACT_PACK_ID} pack spec has no sceneShell`)
  return {
    schemaVersion: CONTRACT_SCHEMA_VERSION,
    packId: CONTRACT_PACK_ID,
    shell: {
      facadeDepth: shell.facadeDepth,
      slabDepth: shell.slabDepth,
      windowBandPitch: shell.windowBandPitch,
    },
    layouts: contractLayouts(layoutsDocument).map((layout) => {
      const geometry = towerShellGeometry(layout, shell)
      return {
        id: layout.id,
        layout: { columns: layout.columns, rows: layout.rows, origin: layout.origin },
        corners: {
          rightCorner: point(geometry.edges.rightCorner, `${layout.id}.rightCorner`),
          frontCorner: point(geometry.edges.frontCorner, `${layout.id}.frontCorner`),
          leftCorner: point(geometry.edges.leftCorner, `${layout.id}.leftCorner`),
        },
        faces: {
          xMax: faceContract(geometry.xMax, shell),
          yMax: faceContract(geometry.yMax, shell),
        },
      }
    }),
  }
}

export async function buildFromRepository() {
  const [layoutsDocument, packSpec] = await Promise.all([
    readFile(fileURLToPath(LAYOUTS_URL), 'utf8').then(JSON.parse),
    readFile(fileURLToPath(PACK_SPEC_URL), 'utf8').then(JSON.parse),
  ])
  return buildTowerShellContract(layoutsDocument, packSpec)
}

export async function readFixture() {
  return JSON.parse(await readFile(fileURLToPath(FIXTURE_URL), 'utf8'))
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.stdout.write(JSON.stringify(await buildFromRepository()))
}
