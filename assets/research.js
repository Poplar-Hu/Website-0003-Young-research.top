/* ============================================================================
   research.who-young.top —— 共享脚本
   ----------------------------------------------------------------------------
   1) 页面骨架交互：导航栏滚动加深、移动端菜单、回到顶部
   2) 项目页：目录高亮、窄屏目录折叠、代码块复制、图片灯箱
   3) 首页：按状态筛选项目卡片
   4) Mermaid：只在页面真的用到时才初始化

   约定：**不写行内样式**，一切视觉状态都靠加类名（样式在 research.css 里）。
   ========================================================================== */

(() => {
  'use strict';

  /* ── 导航栏：滚动后加深 ─────────────────────────────────────────────── */
  const navbar = document.getElementById('navbar');
  if (navbar) {
    const onScroll = () => navbar.classList.toggle('is-scrolled', window.scrollY > 40);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  /* ── 移动端菜单 ─────────────────────────────────────────────────────── */
  const toggle = document.getElementById('menu-toggle');
  const links = document.getElementById('nav-links');
  if (toggle && links) {
    const setOpen = (open) => {
      links.classList.toggle('is-open', open);
      toggle.innerHTML = open ? '<i class="fa fa-times"></i>' : '<i class="fa fa-bars"></i>';
      toggle.setAttribute('aria-expanded', String(open));
    };
    toggle.addEventListener('click', () => setOpen(!links.classList.contains('is-open')));
    links.addEventListener('click', (e) => { if (e.target.tagName === 'A') setOpen(false); });
  }

  /* ── 回到顶部 ───────────────────────────────────────────────────────── */
  const toTop = document.getElementById('to-top');
  if (toTop) {
    toTop.addEventListener('click', (e) => {
      e.preventDefault();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }

  /* ── 轻提示 ─────────────────────────────────────────────────────────── */
  let toastEl = null;
  let toastTimer = null;
  function toast(message, kind) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.className = 'toast';
      document.body.appendChild(toastEl);
    }
    toastEl.classList.remove('is-ok', 'is-error');
    if (kind === 'ok') toastEl.classList.add('is-ok');
    if (kind === 'error') toastEl.classList.add('is-error');
    toastEl.textContent = message;
    // 先移除再重新加上，保证连续点击时动画能重播
    toastEl.classList.remove('is-open');
    void toastEl.offsetWidth;
    toastEl.classList.add('is-open');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('is-open'), 1800);
  }

  /* ── 复制到剪贴板；非安全上下文（http 且非 localhost）时降级 ────────── */
  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch { /* 落到下面的降级分支 */ }

    const ta = document.createElement('textarea');
    ta.value = text;
    ta.className = 'copy-sink';
    ta.setAttribute('readonly', '');
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
    return ok;
  }

  /* ── 项目页：目录高亮 + 窄屏折叠 ────────────────────────────────────── */
  const docToc = document.getElementById('doc-toc');
  if (docToc) {
    const tocTitle = docToc.querySelector('.toc-title');
    // 窄屏下标题就是折叠开关；宽屏点它没有副作用（列表本来就展开着）
    if (tocTitle) {
      tocTitle.addEventListener('click', () => docToc.classList.toggle('is-open'));
    }

    const tocLinks = Array.from(docToc.querySelectorAll('a[href^="#"]'));
    const targets = tocLinks
      .map((a) => document.getElementById(decodeURIComponent(a.getAttribute('href').slice(1))))
      .filter(Boolean);

    if (targets.length && 'IntersectionObserver' in window) {
      // 用「当前视口内最靠上的那个标题」当活跃项。
      // 单纯用 IntersectionObserver 的 isIntersecting 会有多个同时命中，
      // 高亮会来回跳，所以维护一个可见集合、取最靠上的一个。
      const visible = new Set();
      const setActive = () => {
        let best = null;
        for (const el of visible) {
          if (!best || el.getBoundingClientRect().top < best.getBoundingClientRect().top) best = el;
        }
        if (!best) return;
        tocLinks.forEach((a) => {
          a.classList.toggle('is-active', decodeURIComponent(a.getAttribute('href').slice(1)) === best.id);
        });
      };
      const observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) visible.add(entry.target);
          else visible.delete(entry.target);
        }
        setActive();
      }, { rootMargin: '-90px 0px -70% 0px', threshold: 0 });
      targets.forEach((t) => observer.observe(t));

      // 页面刚打开时（还没滚动）也要有一个高亮，否则目录看着是死的
      window.addEventListener('scroll', () => {
        if (window.scrollY < 120 && tocLinks.length) {
          tocLinks.forEach((a, i) => a.classList.toggle('is-active', i === 0));
        }
      }, { passive: true });
    }
  }

  /* ── 代码块：复制按钮 ───────────────────────────────────────────────── */
  /* 文案走 i18n.t()：静态原文由 lang.js 替换，点击时的动态文字这里取，
     两边的兜底串保持一致，切语言后不会一辆中文一辆英文。 */
  const T = (key, fallback) => (window.i18n ? window.i18n.t(key, null, fallback) : fallback);
  document.querySelectorAll('.md-body .code-block').forEach((block) => {
    const code = block.querySelector('pre code');
    const button = block.querySelector('.code-copy');
    if (!code || !button) return;
    let timer = null;
    button.addEventListener('click', async () => {
      const ok = await copyText(code.textContent);
      button.textContent = ok ? T('code-copied', '已复制') : T('code-copyfail', '复制失败');
      button.classList.toggle('is-ok', ok);
      clearTimeout(timer);
      timer = setTimeout(() => {
        button.textContent = T('code-copy', '复制');
        button.classList.remove('is-ok');
      }, 1600);
    });
  });

  /* ── 图片灯箱 ───────────────────────────────────────────────────────── */
  const zooms = Array.from(document.querySelectorAll('.md-body .fig-zoom'));
  if (zooms.length) {
    const box = document.createElement('div');
    box.className = 'lightbox';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.innerHTML = [
      '<button class="lb-close" type="button" aria-label="关闭"><i class="fa fa-times"></i></button>',
      '<div class="lb-box"><img alt=""><div class="lb-cap"></div></div>',
    ].join('');
    document.body.appendChild(box);

    const img = box.querySelector('img');
    const cap = box.querySelector('.lb-cap');
    let lastFocus = null;

    const close = () => {
      box.classList.remove('is-open');
      img.removeAttribute('src');
      if (lastFocus) lastFocus.focus();
    };
    const open = (href, alt, caption) => {
      lastFocus = document.activeElement;
      img.src = href;
      img.alt = alt || '';
      cap.textContent = caption || '';
      box.classList.add('is-open');
      box.querySelector('.lb-close').focus();
    };

    zooms.forEach((a) => {
      a.addEventListener('click', (e) => {
        e.preventDefault();
        const image = a.querySelector('img');
        const figcap = a.parentElement ? a.parentElement.querySelector('figcaption') : null;
        open(a.getAttribute('href'),
             image ? image.alt : '',
             figcap ? figcap.textContent : '');
      });
    });

    box.addEventListener('click', (e) => {
      // 点背景或关闭按钮都关；点图片本身不关
      if (e.target === box || e.target.closest('.lb-close')) close();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && box.classList.contains('is-open')) close();
    });
  }

  /* ── 首页：按状态筛选卡片 ───────────────────────────────────────────── */
  const filterBar = document.getElementById('filter-bar');
  const cardGrid = document.getElementById('record-grid');
  if (filterBar && cardGrid) {
    const chips = Array.from(filterBar.querySelectorAll('.chip'));
    const cards = Array.from(cardGrid.querySelectorAll('.rec-card'));
    const empty = document.getElementById('filter-empty');

    const apply = (status) => {
      let shown = 0;
      cards.forEach((card) => {
        const hit = status === 'all' || card.dataset.status === status;
        card.classList.toggle('is-hidden', !hit);
        if (hit) shown++;
      });
      chips.forEach((c) => c.classList.toggle('is-on', c.dataset.status === status));
      if (empty) empty.classList.toggle('hidden', shown > 0);
      // 让 URL 也能反映筛选，方便把「只看进行中」的链接分享出去
      const url = new URL(window.location.href);
      if (status === 'all') url.searchParams.delete('status');
      else url.searchParams.set('status', status);
      history.replaceState(null, '', url);
    };

    chips.forEach((chip) => {
      chip.addEventListener('click', () => apply(chip.dataset.status));
    });

    // 打开时就按 URL 里的 ?status= 过滤；空结果的提示也要在这一步就位
    const initial = new URLSearchParams(window.location.search).get('status');
    if (initial && chips.some((c) => c.dataset.status === initial)) apply(initial);
    else if (empty) empty.classList.add('hidden');
  }

  /* ══════════════════════════════════════════════════════════════════════
     需要第三方库的两件事：代码高亮、Mermaid 渲染
     ----------------------------------------------------------------------
     这两个库都用 defer 加载（highlight 126 KB、mermaid 2.5 MB，不 defer 会
     卡住首屏），defer 的脚本在本文件之后才执行，所以这里必须等到
     DOMContentLoaded 再判断 window.hljs / window.mermaid ——
     在文件顶层直接判断只会拿到 undefined，那段代码就静默失效了。
     ════════════════════════════════════════════════════════════════════ */
  document.addEventListener('DOMContentLoaded', () => {
    initHighlight();
    initMermaid();
  });

  function initHighlight() {
    if (!window.hljs) return;
    document.querySelectorAll('.md-body pre code').forEach((el) => {
      try {
        window.hljs.highlightElement(el);
      } catch {
        /* 单个块失败不影响其它块 */
      }
    });
  }

  function initMermaid() {
    const blocks = Array.from(document.querySelectorAll('.md-body .mermaid'));
    if (!blocks.length || !window.mermaid) return;

    const render = async () => {
      try {
        window.mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          theme: 'base',
          fontFamily: 'inherit',
          themeVariables: {
            primaryColor: '#E1F5FE',
            primaryTextColor: '#1E293B',
            primaryBorderColor: '#3B82F6',
            lineColor: '#94A3B8',
            secondaryColor: '#F3E5F5',
            tertiaryColor: '#F5F7FA',
          },
        });
        await window.mermaid.run({ nodes: blocks });
        blocks.forEach((node) => {
          const wrap = node.closest('.mermaid-wrap');
          if (wrap) wrap.classList.remove('is-loading');
        });
      } catch (error) {
        // 语法错误时 mermaid 会留下一块空的画布，这里换成能读的提示
        blocks.forEach((node) => {
          const wrap = node.closest('.mermaid-wrap');
          if (!wrap || wrap.querySelector('svg')) return;
          wrap.classList.add('is-error');
          wrap.classList.remove('is-loading');
          const pre = document.createElement('pre');
          pre.textContent = String(error && error.message ? error.message : error);
          wrap.appendChild(pre);
        });
      }
    };
    render();
  }

  window.Research = { toast, copy: copyText };
})();
