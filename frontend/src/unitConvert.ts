/**
 * 平台单位换算
 * =============
 * Temu 的提交接口用整数小单位，表单用人类可读单位。两边各存一份，
 * 历史上因为「回填读了平台单位」「只改 input 没同步 value」踩过两次坑：
 *   - 体积 10cm 显示成 100
 *   - 重量 50g 显示成 50000
 *
 * 约定：所有换算都走这里，组件里不要再出现 `* 10` / `* 1000` / `* 100`。
 *
 *   体积   cm ↔ mm    ×10
 *   重量   g  ↔ mg    ×1000
 *   价格   元 ↔ 分    ×100
 */

export const VOLUME_SCALE = 10;
export const WEIGHT_SCALE = 1000;
export const PRICE_SCALE = 100;

/** 体积：用户输入的 cm 值 */
export interface VolumeInput {
  len: number;
  width: number;
  height: number;
}

/** 体积：提交给平台时同时带两套，input* 用于回填 */
export interface VolumePayload {
  /** 毫米 */
  len: number;
  width: number;
  height: number;
  /** 用户输入的 cm 原值 */
  inputLen: string;
  inputWidth: string;
  inputHeight: string;
}

/** 重量：用户输入的 g 值 */
export interface WeightInput {
  value: number;
}

/** 重量：提交给平台时同时带两套 */
export interface WeightPayload {
  /** 毫克 */
  value: number;
  /** 用户输入的 g 原值 */
  inputValue: string;
  inputUnit: 'g';
}

const toNum = (v: unknown, fallback = 0): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
};

export function volumeToPayload(input: VolumeInput): VolumePayload {
  const len = toNum(input.len, 10);
  const width = toNum(input.width, 9);
  const height = toNum(input.height, 8);
  return {
    len: Math.round(len * VOLUME_SCALE),
    width: Math.round(width * VOLUME_SCALE),
    height: Math.round(height * VOLUME_SCALE),
    inputLen: String(len),
    inputWidth: String(width),
    inputHeight: String(height),
  };
}

/**
 * 平台体积 → 用户单位。
 * 优先读 input*；老数据只有 len（毫米）时按 ÷10 还原。
 */
export function volumeFromPayload(payload: unknown): VolumeInput {
  const p = (payload || {}) as Record<string, unknown>;
  return {
    len: toNum(p.inputLen, toNum(p.len) / VOLUME_SCALE),
    width: toNum(p.inputWidth, toNum(p.width) / VOLUME_SCALE),
    height: toNum(p.inputHeight, toNum(p.height) / VOLUME_SCALE),
  };
}

/**
 * 编辑保存前重算平台单位。
 * 编辑弹窗改的是 input*，不同步重算 len/width/height 的话，存进去还是旧值。
 */
export function normalizeVolumePayload<T extends Record<string, unknown>>(payload: T): T {
  const hasInput = payload.inputLen !== undefined || payload.inputWidth !== undefined;
  if (!hasInput) return payload;
  return {
    ...payload,
    len: Math.round(toNum(payload.inputLen, toNum(payload.len) / VOLUME_SCALE) * VOLUME_SCALE),
    width: Math.round(toNum(payload.inputWidth, toNum(payload.width) / VOLUME_SCALE) * VOLUME_SCALE),
    height: Math.round(toNum(payload.inputHeight, toNum(payload.height) / VOLUME_SCALE) * VOLUME_SCALE),
  };
}

export function weightToPayload(input: WeightInput): WeightPayload {
  const g = toNum(input.value, 200);
  return {
    value: Math.round(g * WEIGHT_SCALE),
    inputValue: String(g),
    inputUnit: 'g',
  };
}

/** 平台重量 → 用户单位（优先 inputValue，老数据按 ÷1000 还原） */
export function weightFromPayload(payload: unknown): WeightInput {
  const p = (payload || {}) as Record<string, unknown>;
  return { value: toNum(p.inputValue, toNum(p.value) / WEIGHT_SCALE) };
}

/** 编辑保存前重算毫克值 */
export function normalizeWeightPayload<T extends Record<string, unknown>>(payload: T): T {
  if (payload.inputValue === undefined) return payload;
  return {
    ...payload,
    value: Math.round(toNum(payload.inputValue, toNum(payload.value) / WEIGHT_SCALE) * WEIGHT_SCALE),
  };
}

/** 元 → 分（提交给平台） */
export const yuanToCents = (yuan: unknown): number => Math.round(toNum(yuan) * PRICE_SCALE);

/** 分 → 元（回填） */
export const centsToYuan = (cents: unknown): number => toNum(cents) / PRICE_SCALE;
