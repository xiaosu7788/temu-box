/**
 * Bridge 自检（不联网、不登录真实账号）
 * =====================================
 * 验证多账号改造的关键不变量：
 *   1. 进程能起来、/ping 可用
 *   2. 缺 token → 403；带 token → 通过
 *   3. 未登录账号的 /goods/list → 401 NOT_LOGIN（不会被"自动登录"糊过去）
 *   4. 缺 account → 400 ARGS
 *   5. /session/login 缺密码 → 400，且不产生会话
 *   6. /session/logout 对未登录账号是幂等的
 *   7. /session/list（/health 汇总）只回脱敏账号
 *   8. 已移除的上游接口（/api 直通、/orders、/region）一律 404
 *
 * 用法：node test/smoke.js
 */
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');

const PORT = Number(process.env.SMOKE_PORT || 18898);
const TOKEN = 'smoke-token';
const BASE = { host: '127.0.0.1', port: PORT };

let failures = 0;

function request(method, pathname, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body), 'utf8');
    const req = http.request(
      {
        ...BASE,
        method,
        path: pathname,
        headers: {
          ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': payload.length } : {}),
          ...headers,
        },
        timeout: 5000,
      },
      (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          let json = null;
          try { json = JSON.parse(text); } catch (e) { }
          resolve({ status: res.statusCode, json, text });
        });
      },
    );
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('timeout')));
    if (payload) req.write(payload);
    req.end();
  });
}

function check(name, condition, extra) {
  if (condition) {
    console.log(`  ok   ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL ${name}${extra ? ' :: ' + JSON.stringify(extra) : ''}`);
  }
}

async function waitReady(tries = 60) {
  for (let i = 0; i < tries; i += 1) {
    try {
      const res = await request('GET', '/ping');
      if (res.status === 200) return true;
    } catch (e) { }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return false;
}

(async () => {
  const child = spawn(process.execPath, [path.join(__dirname, '..', 'server.js')], {
    env: {
      ...process.env,
      PORT: String(PORT),
      HOST: '127.0.0.1',
      BRIDGE_TOKEN: TOKEN,
      LOG: '0',
      TEMU_OPS_DATA: path.join(__dirname, '.smoke-data'),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });

  try {
    const ready = await waitReady();
    check('进程启动并响应 /ping', ready, stderr.slice(-400));
    if (!ready) return;

    const ping = await request('GET', '/ping');
    check('/ping 不需要 token', ping.status === 200 && ping.json.ok === true);

    const noToken = await request('GET', '/health');
    check('缺 token → 403', noToken.status === 403 && noToken.json.error.code === 'FORBIDDEN');

    const badToken = await request('GET', '/health', undefined, { 'X-Bridge-Token': 'wrong' });
    check('错误 token → 403', badToken.status === 403);

    const auth = { 'X-Bridge-Token': TOKEN };

    const summary = await request('GET', '/health', undefined, auth);
    check('/health 汇总可用', summary.status === 200 && summary.json.data.total === 0);

    const noAccount = await request('GET', '/goods/list', undefined, auth);
    check('商品列表缺 account → 400', noAccount.status === 400 && noAccount.json.error.code === 'ARGS');

    const notLogin = await request('GET', '/goods/list?account=smoke%40test.com', undefined, auth);
    check('未登录账号 → 401 NOT_LOGIN', notLogin.status === 401 && notLogin.json.error.code === 'NOT_LOGIN');

    // 卡片接口：新增 /kefu/cards，且 /kefu/card 接受 toolId / maxPages 参数
    const cardsNoAccount = await request('GET', '/kefu/cards', undefined, auth);
    check('/kefu/cards 缺 account → 400', cardsNoAccount.status === 400 && cardsNoAccount.json.error.code === 'ARGS');

    const cardsNotLogin = await request('GET', '/kefu/cards?account=smoke%40test.com', undefined, auth);
    check('/kefu/cards 未登录 → 401', cardsNotLogin.status === 401 && cardsNotLogin.json.error.code === 'NOT_LOGIN');

    const cardWithParams = await request(
      'GET',
      '/kefu/card?account=smoke%40test.com&toolId=2406230000031&maxPages=3',
      undefined,
      auth,
    );
    check(
      '/kefu/card 接受 toolId/maxPages（未登录仍 401，参数不报错）',
      cardWithParams.status === 401 && cardWithParams.json.error.code === 'NOT_LOGIN',
    );

    const wakeNoTool = await request('POST', '/kefu/wake', { account: 'smoke@test.com' }, auth);
    check('/kefu/wake 缺 toolName → 400', wakeNoTool.status === 400 && wakeNoTool.json.error.code === 'ARGS');

    const wakeNotLogin = await request('POST', '/kefu/wake', { account: 'smoke@test.com', toolName: '商品发布到站点' }, auth);
    check('/kefu/wake 未登录 → 401', wakeNotLogin.status === 401 && wakeNotLogin.json.error.code === 'NOT_LOGIN');

    const loginMissing = await request('POST', '/session/login', { account: 'smoke@test.com' }, auth);
    check('登录缺密码 → 400 且不建会话', loginMissing.status === 400);

    const afterFailed = await request('GET', '/health?account=smoke%40test.com', undefined, auth);
    check(
      '失败登录后账号显示为未登录且已脱敏',
      afterFailed.status === 200
      && afterFailed.json.data.alive === false
      && afterFailed.json.data.account === 's***@test.com',
      afterFailed.json,
    );

    const logout = await request('POST', '/session/logout', { account: 'smoke@test.com' }, auth);
    check('未登录账号 logout 幂等', logout.status === 200 && logout.json.data.loggedOut === true);

    const passthrough = await request('POST', '/api', { path: '/api/seller/auth/userInfo' }, auth);
    check('已移除的 /api 直通 → 404', passthrough.status === 404);

    const orders = await request('GET', '/orders/list?account=x', undefined, auth);
    check('已移除的 /orders → 404', orders.status === 404);

    const region = await request('POST', '/region/switch', { account: 'x', region: 'us' }, auth);
    check('已移除的 /region/switch → 404', region.status === 404);

    const locked = await request('GET', '/session/list', undefined, auth);
    check('/session/list 不存在（用 /health 汇总替代）', locked.status === 404);
  } finally {
    child.kill();
    await new Promise((resolve) => setTimeout(resolve, 200));
  }

  console.log('');
  if (failures) {
    console.log(`FAILED: ${failures} 项未通过`);
    process.exit(1);
  }
  console.log('smoke: all checks passed');
})().catch((e) => {
  console.error('smoke 运行出错:', e);
  process.exit(1);
});
