/* ============================================================================
   多语言（i18n）—— 科研记录站唯一的语言实现
   ----------------------------------------------------------------------------
   与主站 who-young.top 的 src/lang.js、工具站 assets/lang.js 是同一套机制，
   三份文件只差语言包路径与少量接口，改动时请三处一起看。

   设计要点
   1. 唯一机制：data-i18n="键名" + lang/{cn,en,jp}.json
   2. 不阻塞首屏：HTML 里已写好中文原文作为兜底，语言包到达后原地替换。
      禁用 JS 或语言包加载失败时，页面仍是完整中文。
   3. <title> / <meta content> / 输入框 placeholder 都支持。
   4. 语言包路径用根路径 /lang/ —— 项目页在 /<slug>/ 这样的子目录下，
      相对路径会被解析成 /<slug>/lang/... 而 404。

   现状：正文只有中文，en/jp 语言包是 cn 的镜像（生成器在缺失时创建），
   所以非中文浏览器不会 404，也不会看到「翻译了一半」的界面。
   等真的要出英文版时，把 en.json 里的值换成英文即可。
   ========================================================================== */

(() => {
  'use strict';

  const DEFAULT_LANG = 'cn';
  const SUPPORTED_LANGS = ['cn', 'en', 'jp'];
  const STORAGE_KEY = 'preferred-lang';
  const LANG_PATH = '/lang/';

  /** 语言标识 → <html lang> 取值 */
  const HTML_LANG = { cn: 'zh-CN', en: 'en', jp: 'ja' };

  /** 已下载的语言包，避免重复请求 */
  const packCache = new Map();

  /** 当前生效的语言与语言包 */
  let currentLang = DEFAULT_LANG;
  let currentPack = null;
  const readyCallbacks = [];

  /** 取用户偏好：URL 参数 → 本地存储 → 浏览器语言 → 默认 */
  function detectLang() {
    // ?lang=en 属于显式指定，优先级最高（也便于分享指定语言的链接）
    try {
      const forced = new URLSearchParams(location.search).get('lang');
      if (forced && SUPPORTED_LANGS.includes(forced)) return forced;
    } catch {
      /* 忽略 */
    }

    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved && SUPPORTED_LANGS.includes(saved)) return saved;
    } catch {
      /* 隐私模式下 localStorage 可能抛异常，忽略即可 */
    }

    for (const tag of navigator.languages || [navigator.language || '']) {
      const lower = String(tag).toLowerCase();
      if (lower.startsWith('zh')) return 'cn';
      if (lower.startsWith('ja')) return 'jp';
      if (lower.startsWith('en')) return 'en';
    }
    return DEFAULT_LANG;
  }

  /** 下载语言包（带缓存） */
  async function fetchPack(lang) {
    if (packCache.has(lang)) return packCache.get(lang);
    const response = await fetch(`${LANG_PATH}${lang}.json`, { cache: 'no-cache' });
    if (!response.ok) throw new Error(`${lang}.json 请求失败：HTTP ${response.status}`);
    const data = await response.json();
    packCache.set(lang, data);
    return data;
  }

  /**
   * 把语言包渲染到页面
   * - 普通元素：含 HTML 标签则用 innerHTML，否则用 textContent（更安全、更快）
   * - <title>：更新 document.title
   * - <meta>：更新 content 属性
   * - 带 data-i18n-placeholder 的元素：更新 placeholder 属性
   */
  function render(pack) {
    document.querySelectorAll('[data-i18n]').forEach((element) => {
      const value = pack[element.getAttribute('data-i18n')];
      if (typeof value !== 'string') return;

      switch (element.tagName) {
        case 'TITLE':
          document.title = value;
          break;
        case 'META':
          element.setAttribute('content', value);
          break;
        default:
          if (value.includes('<')) element.innerHTML = value;
          else element.textContent = value;
      }
    });

    document.querySelectorAll('[data-i18n-placeholder]').forEach((element) => {
      const value = pack[element.getAttribute('data-i18n-placeholder')];
      if (typeof value === 'string') element.setAttribute('placeholder', value);
    });
  }

  /** 让所有语言选择器（class="language-selector"）显示当前语言 */
  function syncSelectors(lang) {
    document.querySelectorAll('.language-selector').forEach((select) => {
      select.value = lang;
    });
  }

  /** 切换语言 */
  async function setLang(lang) {
    const target = SUPPORTED_LANGS.includes(lang) ? lang : DEFAULT_LANG;
    try {
      const pack = await fetchPack(target);
      currentLang = target;
      currentPack = pack;
      render(pack);
      document.documentElement.lang = HTML_LANG[target];
      try {
        localStorage.setItem(STORAGE_KEY, target);
      } catch {
        /* 忽略存储不可用 */
      }
      // 语言包就绪（或切换）后，通知需要重建动态文案的地方
      readyCallbacks.forEach((fn) => fn(target, pack));
    } catch (error) {
      // 语言包拿不到就保留 HTML 里的中文原文，不破坏页面
      console.warn('[i18n] 语言包加载失败，保留页面原文：', error.message);
    }
    syncSelectors(target);
  }

  /** 绑定选择器事件（全站只有这一处绑定，因此无需克隆节点去重） */
  function bindSelectors() {
    document.querySelectorAll('.language-selector').forEach((select) => {
      select.addEventListener('change', (event) => setLang(event.target.value));
    });
  }

  /**
   * 取一条文案。
   *   t('card-words', { n: 1234 })  →  "1234 字"
   * 语言包还没到位时返回 fallback，再没有就返回 key 本身。
   */
  function t(key, vars, fallback) {
    let text = (currentPack && currentPack[key]) || fallback || key;
    if (vars) {
      for (const k of Object.keys(vars)) text = text.split(`{${k}}`).join(vars[k]);
    }
    return text;
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindSelectors();
    // HTML 原文即中文兜底，先同步选择器再按偏好语言覆盖渲染
    syncSelectors(DEFAULT_LANG);
    setLang(detectLang());
  });

  // 便于调试；页面脚本用 i18n.t() 取动态文案
  window.i18n = {
    setLang,
    t,
    current: () => currentLang,
    /** 语言包就绪时回调；若已就绪则立即执行。之后每次切换语言都会再触发 */
    onReady(fn) {
      if (currentPack) fn(currentLang, currentPack);
      else readyCallbacks.push(fn);
    },
  };
})();
