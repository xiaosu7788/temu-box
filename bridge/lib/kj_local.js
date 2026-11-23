/**
 * KJ 本地登录适配器
 * ==================
 * 用 fast_login 在**进程内**完成 KJ 密码登录，不依赖 login_service HTTP 服务。
 * 登录成功后把会话写入 runtime/session_*.json，供 GloClient.loadKjCookies() 读取。
 *
 * 接口与 kj_sdk 保持一致：login(account, password, opts)
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const fast = require('./fast_login');

const DATA_DIR = process.env.TEMU_OPS_DATA || path.join(__dirname, '..', 'runtime');

function sessionFile(account) {
  const key = String(account || '').trim().toLowerCase();
  const h = crypto.createHash('sha1').update(key).digest('hex').slice(0, 16);
  return path.join(DATA_DIR, 'session_' + h + '.json');
}

/**
 * 登录
 * @param {string} account
 * @param {string} password
 * @param {object} opts { force }
 * @returns {{ok:boolean, cookies:object, res?:object}}
 */
async function login(account, password, opts = {}) {
  if (!account || !password) throw new Error('缺少账号或密码');

  const r = await fast.login(account, password, opts);

  // ⚠️ KJ 登录失败时 HTTP 仍是 200，风控场景还会下发 cookie。
  //    只看「有没有 cookie」会把「密码错 / 需要验证码」误报成「登录过期或未登录」。
  const errorCode = r && r.res ? r.res.errorCode : undefined;
  const okFlag = Boolean(r && (r.ok || (r.res && r.res.success === true)));
  if (!okFlag) {
    const detail = (r && r.res && (r.res.errorMsg || r.res.errorMsgI18n || r.res.msg)) || 'KJ 登录未成功';
    const suffix = errorCode !== undefined && errorCode !== null ? `（errorCode=${errorCode}）` : '';
    const error = new Error(`KJ 登录失败：${detail}${suffix}`);
    error.code = String(errorCode) === '54001' ? 'CAPTCHA' : 'KJ_LOGIN';
    error.errorCode = errorCode;
    throw error;
  }

  const cookies = r && r.jar && typeof r.jar.dump === 'function' ? r.jar.dump() : null;
  if (!cookies || !Object.keys(cookies).length) {
    throw new Error('登录未返回 cookie');
  }

  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(sessionFile(account), JSON.stringify({
    at: Date.now(),
    account: String(account),
    cookies,
  }, null, 2));

  return { ok: true, cookies, res: r.res, log: r.log };
}

/** 读取某账号的会话 */
function loadSession(account) {
  const f = sessionFile(account);
  if (!fs.existsSync(f)) return null;
  try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch (e) { return null; }
}

/** 删除会话 */
function dropSession(account) {
  const f = sessionFile(account);
  try { if (fs.existsSync(f)) fs.unlinkSync(f); } catch (e) { }
}

module.exports = { login, loadSession, dropSession, sessionFile, DATA_DIR };
