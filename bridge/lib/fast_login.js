/**
 * 优化版纯协议登录客户端
 *
 * 相对原版的优化:
 *   1. keepAlive https.Agent      —— 连接复用，200ms -> 40ms/请求
 *   2. serverTime 缓存 + 时钟偏移  —— 跳过 /api/server/_stm，省 200~1400ms
 *   3. 生成器单例                  —— SDK 只加载一次，generate() 只要 9~29ms
 *   4. 启动预热                    —— 后台并发预取 serverTime / key
 *   5. 请求流水线                  —— key 与 serverTime 并发
 */
const fs = require('fs');
const path = require('path');
const https = require('https');
const crypto = require('crypto');

const PROJ = process.env.TEMU_OPS_ANTI || path.join(__dirname, '..', 'runtime', 'anti_content');
const BASE = 'https://seller.kuajingmaihuo.com';
const HOST = 'seller.kuajingmaihuo.com';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0';

// ============ 1. 连接池 ============
const agent = new https.Agent({
  keepAlive: true,
  maxSockets: 8,
  keepAliveMsecs: 30000,
  timeout: 30000,
});

// ============ 2. 时钟偏移 ============
const clock = { offset: 0, syncedAt: 0 };
function serverNow() { return Date.now() + clock.offset; }

function request(method, pathname, headersIn, body, jar) {
  return new Promise(resolve => {
    const t0 = Date.now();
    const headers = Object.assign({
      'Accept': 'application/json, text/plain, */*',
      'Accept-Language': 'zh-CN,zh;q=0.9',
      'Origin': BASE, 'Referer': BASE + '/login',
      'User-Agent': UA,
      'sec-ch-ua': '"Chromium";v="156", "Microsoft Edge";v="156", "Not)A;Brand";v="24"',
      'sec-ch-ua-mobile': '?0', 'sec-ch-ua-platform': '"Windows"',
      'Connection': 'keep-alive',
    }, headersIn || {});
    if (jar) { const c = jar.str(); if (c) headers['Cookie'] = c; }
    let p = null;
    if (body !== undefined && body !== null) {
      p = Buffer.from(typeof body === 'string' ? body : JSON.stringify(body), 'utf8');
      headers['Content-Type'] = headers['Content-Type'] || 'application/json';
      headers['Content-Length'] = p.length;
    }
    const r = https.request({ hostname: HOST, port: 443, path: pathname, method, headers, agent, timeout: 25000 }, res => {
      const cs = []; res.on('data', x => cs.push(x));
      res.on('end', () => {
        if (jar) jar.eat(res.headers['set-cookie']);
        resolve({ status: res.statusCode, text: Buffer.concat(cs).toString('utf8'), ms: Date.now() - t0 });
      });
    });
    r.on('error', e => resolve({ status: 0, text: '', ms: Date.now() - t0, err: e.message }));
    r.on('timeout', () => { r.destroy(); resolve({ status: 0, text: '', ms: Date.now() - t0, err: 'timeout' }); });
    if (p) r.write(p);
    r.end();
  });
}

// ============ 3. Cookie 罐 ============
class Jar {
  constructor(init) { this.map = Object.assign({}, init || {}); }
  str() { return Object.entries(this.map).map(([k, v]) => `${k}=${v}`).join('; '); }
  set(k, v) { this.map[k] = v; }
  eat(sc) {
    if (!sc) return;
    for (const raw of (Array.isArray(sc) ? sc : [sc])) {
      const p = String(raw).split(';')[0];
      const i = p.indexOf('=');
      if (i > 0) this.map[p.slice(0, i).trim()] = p.slice(i + 1).trim();
    }
  }
  dump() { return { ...this.map }; }
}

// ============ 4. 生成器单例 + 预热 ============
let _gen = null;
let _keyCache = null;
let _warm = null;

async function syncClock(jar) {
  const t0 = Date.now();
  const r = await request('GET', '/api/server/_stm', {}, null, jar);
  try {
    const j = JSON.parse(r.text);
    const st = j.server_time;
    if (typeof st === 'number' && st > 0) {
      const rtt = Date.now() - t0;
      clock.offset = st - (t0 + Math.floor(rtt / 2));   // 取往返中点
      clock.syncedAt = Date.now();
      return true;
    }
  } catch (e) { }
  return false;
}

/** 预热: 先用连接池同步时钟（快），再把时间传给 SDK（跳过它内部的慢 fetch） */
async function warmup(jar) {
  if (_warm) return _warm;
  const t0 = Date.now();
  const acMod = require(path.join(PROJ, 'anti_content.js'));

  // 1. 连接池同步时钟（约 40~230ms）
  const ok = await syncClock(jar);

  // 2. 用同步好的时间创建 SDK（不会触发内部 fetch）
  _gen = await acMod.createAntiContent({ serverTime: serverNow() });
  _gen.setServerTime(serverNow());

  _warm = { gen: _gen, clockOk: ok, initMs: Date.now() - t0 };
  return _warm;
}

async function getKey(jar, force) {
  if (_keyCache && !force && Date.now() - _keyCache.at < 5 * 60 * 1000) return _keyCache;
  const r = await request('POST', '/bg/quiet/api/mms/key/login', {}, {}, jar);
  const j = JSON.parse(r.text);
  _keyCache = { publicKey: j.result.publicKey, version: j.result.version, at: Date.now(), ms: r.ms };
  return _keyCache;
}

function rsaEncrypt(pwd, pkB64) {
  const pem = '-----BEGIN PUBLIC KEY-----\n' + pkB64.match(/.{1,64}/g).join('\n') + '\n-----END PUBLIC KEY-----';
  return crypto.publicEncrypt({ key: pem, padding: crypto.constants.RSA_PKCS1_PADDING }, Buffer.from(pwd)).toString('base64');
}

// ============ 5. 登录 ============
/**
 * 判断登录名的类型（Temu 登录接口的 contactType）
 *   0 = Account（手机号 / 账号）
 *   1 = Email
 *   2 = Scan（扫码）
 *
 * ⚠️ 默认按 Email 提交会把手机号判成「邮箱格式不对」（errorCode=40002012）。
 */
function detectContactType(account) {
  const value = String(account || '').trim();
  if (!value) return 0;
  if (value.includes('@')) return 1;
  if (/^\+?[\d\s-]{6,20}$/.test(value)) return 0;
  return 0;
}

/** 手机号去掉 +86 / 空格 / 横杠（Temu 只认 11 位号码）；邮箱与用户名原样 */
function normalizeLoginName(account) {
  const value = String(account || '').trim();
  if (value.includes('@') || /[a-zA-Z]/.test(value)) return value;
  return value.replace(/[\s-]/g, '').replace(/^\+?86(?=\d{11}$)/, '');
}

async function login(account, password, opts = {}) {
  const jar = opts.jar || new Jar();
  const log = [];
  const mark = (label, t0) => { const d = Date.now() - t0; log.push([label, d]); return d; };

  let t = Date.now();
  const w = await warmup(jar);
  mark('warmup (SDK+时钟)', t);
  if (w.gen && !opts.keepServerTime) w.gen.setServerTime(serverNow());

  // SDK 写入的 cookie
  for (const kv of w.gen.cookieHeader().split('; ')) {
    const i = kv.indexOf('='); if (i > 0) jar.set(kv.slice(0, i), kv.slice(i + 1));
  }

  t = Date.now();
  const [key, ac] = await Promise.all([
    getKey(jar),
    Promise.resolve().then(() => w.gen.generate()),
  ]);
  mark('key+anti-content', t);

  t = Date.now();
  const enc = rsaEncrypt(password, key.publicKey);
  mark('RSA', t);

  t = Date.now();
  const lr = await request('POST', '/bg/quiet/api/mms/login', {
    'anti-content': ac,
    'x-date': new Date(serverNow()).toISOString().replace(/\.\d{3}Z$/, '.000Z'),
  }, {
    loginName: normalizeLoginName(account),
    contactType: opts.contactType !== undefined ? opts.contactType : detectContactType(account),
    encryptPassword: enc,
    keyVersion: key.version,
    softwareLicenseAgreement: 'https://bdl.cdnfe.com/supplier-public-file-tag/21f0660960/3e93f6d962e48da49cedb07693e13ffe.html',
    privacyPolicy: 'https://bdl.cdnfe.com/upload_bdl/seller-protocol/81ba96c1-c0f0-4e1c-a101-440313936a98',
    // 验证码登录：实测只需 loginName + verifyCode，不需要 verificationId
    ...(opts.verifyCode ? { verifyCode: String(opts.verifyCode) } : {}),
  }, jar);
  mark('/mms/login', t);

  let res = {};
  try { res = JSON.parse(lr.text); } catch (e) { }
  return { ok: String(res.errorCode) === '1000000', res, jar, log, acLen: ac.length };
}

/**
 * 发送登录验证码
 * @param {string} account 邮箱或手机号
 * @param {object} opts { contactType, jar }
 * @returns {{ok:boolean, res:object, contactType:number}}
 *
 * 上游实测：POST /bg/quiet/api/mms/loginVerifyCode
 *   邮箱 → { email }
 *   手机 → { mobile }
 * 成功返回 result: null，不带任何 id
 */
async function sendCode(account, opts = {}) {
  const jar = opts.jar || new Jar();
  const w = await warmup(jar);
  if (w.gen && !opts.keepServerTime) w.gen.setServerTime(serverNow());
  for (const kv of w.gen.cookieHeader().split('; ')) {
    const i = kv.indexOf('=');
    if (i > 0) jar.set(kv.slice(0, i), kv.slice(i + 1));
  }

  const ac = w.gen.generate();
  const contactType = opts.contactType !== undefined ? opts.contactType : detectContactType(account);
  const name = normalizeLoginName(account);
  const body = contactType === 1 ? { email: name } : { mobile: name };

  const r = await request('POST', '/bg/quiet/api/mms/loginVerifyCode', {
    'anti-content': ac,
    'x-date': new Date(serverNow()).toISOString().replace(/\.\d{3}Z$/, '.000Z'),
  }, body, jar);

  let res = {};
  try { res = JSON.parse(r.text); } catch (e) { /* 保留空对象 */ }
  return {
    ok: String(res.errorCode) === '1000000' || res.success === true,
    res,
    contactType,
  };
}

// ============ 6. 业务接口（已登录后：纯 HTTP + cookie，无需 SDK / token）============
// 注意: 业务接口返回 snake_case (error_code) ；登录接口返回 camelCase (errorCode)
function normCode(j) {
  if (!j || typeof j !== 'object') return undefined;
  return j.errorCode !== undefined ? j.errorCode : j.error_code;
}

async function userInfo(jar) {
  const r = await request('POST', '/bg/quiet/api/mms/userInfo', {}, {}, jar);
  let j;
  try { j = JSON.parse(r.text); } catch (e) { j = { raw: r.text }; }
  j.__code = normCode(j);
  j.__status = r.status;
  j.__ms = r.ms;
  return j;
}

/** 通用业务请求：不带 anti-content */
async function callApi(jar, method, pathname, body) {
  const r = await request(method, pathname, {}, body === undefined ? {} : body, jar);
  let j;
  try { j = JSON.parse(r.text); } catch (e) { j = { raw: r.text }; }
  j.__code = normCode(j);
  j.__status = r.status;
  j.__ms = r.ms;
  return j;
}

module.exports = { login, sendCode, userInfo, warmup, syncClock, serverNow, Jar, request, agent, detectContactType, normalizeLoginName };

// ============ CLI ============
if (require.main === module) {
  (async () => {
    const T0 = Date.now();
    console.log('=== 优化版登录 ===');
    const r = await login(process.env.KJ_ACCOUNT, process.env.KJ_PASSWORD);
    console.log(`  errorCode: ${r.res.errorCode} | success: ${r.res.success || r.ok} | anti-content: ${r.acLen} chars`);
    console.log('\n  分段:');
    for (const [l, d] of r.log) console.log(`    ${l.padEnd(24)} ${d}ms`);
    console.log(`    ${'总计'.padEnd(24)} ${Date.now() - T0}ms`);

    if (r.ok) {
      const u = await userInfo(r.jar);
      const rr = u.result || {};
      console.log(`\n  userInfo: ${u.errorCode} | userId=${rr.userId} | ${rr.nickName}`);
      console.log(`  cookies: ${Object.keys(r.jar.dump()).join(', ')}`);
    }
  })().catch(e => console.error('ERR', e.message));
}
