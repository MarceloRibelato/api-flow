import re
import unicodedata

EPHEMERAL_MESSAGE_OBSERVER_SCRIPT = r"""
(() => {
    if (window.__flowEphemeralObserverInstalled) return;
    window.__flowEphemeralObserverInstalled = true;
    window.__flowEphemeralMap = window.__flowEphemeralMap || new Map();

    const MAX_ENTRIES = 1000;
    const MAX_AGE_MS = 30000;
    const TOAST_SELECTOR = '[role="alert"], [role="status"], [aria-live], .Toastify__toast, .toast, .snackbar, .ant-message, .ant-notification, .el-message, .MuiSnackbar-root, .MuiAlert-root, .notistack-SnackbarContainer, [data-sonner-toast], .swal2-popup';

    const pushText = (rawText) => {
        if (!rawText) return;
        const txt = String(rawText).replace(/\s+/g, ' ').trim();
        if (txt.length < 2 || txt.length > 1000) return;

        const now = Date.now();
        const map = window.__flowEphemeralMap;
        if (map.has(txt)) {
            map.delete(txt);
        }
        map.set(txt, now);

        if (map.size > MAX_ENTRIES) {
            const cutoff = now - MAX_AGE_MS;
            for (const [k, ts] of map.entries()) {
                if (ts < cutoff || map.size > MAX_ENTRIES) {
                    map.delete(k);
                } else {
                    break;
                }
            }
        }
    };

    const scanNode = (node) => {
        if (!node || node.nodeType !== 1) return;
        try {
            const aria = node.getAttribute && node.getAttribute('aria-label');
            if (aria) pushText(aria);

            const text = node.innerText || node.textContent || '';
            if (text && text.trim()) {
                pushText(text);
            }

            if (node.querySelectorAll) {
                const toastChildren = node.querySelectorAll(TOAST_SELECTOR);
                for (const child of toastChildren) {
                    const cText = child.innerText || child.textContent || '';
                    if (cText && cText.trim()) pushText(cText);
                }
            }
        } catch (e) {}
    };

    const observerCallback = (mutations) => {
        for (const m of mutations) {
            if (m.type === 'childList') {
                for (const added of m.addedNodes) {
                    if (added.nodeType === 1) {
                        scanNode(added);
                    } else if (added.nodeType === 3 && added.textContent) {
                        pushText(added.textContent);
                    }
                }
            } else if (m.type === 'characterData') {
                const parent = m.target && m.target.parentElement;
                if (parent) {
                    pushText(parent.innerText || parent.textContent || m.target.textContent);
                } else if (m.target && m.target.textContent) {
                    pushText(m.target.textContent);
                }
            } else if (m.type === 'attributes' && m.target && m.target.nodeType === 1) {
                scanNode(m.target);
            }
        }
    };

    const observeTarget = (target) => {
        if (!target) return;
        try {
            const observer = new MutationObserver(observerCallback);
            observer.observe(target, {
                childList: true,
                subtree: true,
                characterData: true,
                attributes: true,
                attributeFilter: ['class', 'style', 'hidden', 'aria-hidden', 'data-state', 'open']
            });
        } catch (e) {}
    };

    observeTarget(document);

    // Also observe any Shadow DOM roots created by Web Components / UI libraries
    try {
        const origAttachShadow = Element.prototype.attachShadow;
        if (origAttachShadow) {
            Element.prototype.attachShadow = function (init) {
                const root = origAttachShadow.call(this, init);
                observeTarget(root);
                return root;
            };
        }
    } catch (e) {}

    window.__getFlowEphemeralList = (windowMs = 25000) => {
        const cutoff = Date.now() - windowMs;
        const result = [];
        for (const [txt, ts] of window.__flowEphemeralMap.entries()) {
            if (ts >= cutoff) {
                result.push(txt);
            }
        }
        return result;
    };

    window.__getFlowEphemeralText = (windowMs = 25000) => {
        return window.__getFlowEphemeralList(windowMs).join('\n');
    };
})();
"""


def _norm(text: str) -> str:
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", s).strip().lower()


def extract_selector_text_hint(selector: str) -> str:
    """Extracts literal text from Playwright/XPath/CSS selectors."""
    if not selector:
        return ""
    s = str(selector).strip()
    patterns = [
        r':has-text\(\s*["\'](.+?)["\']\s*\)',
        r'^text\s*=\s*["\']?(.+?)["\']?$',
        r'contains\(\s*(?:text\(\)|\.|normalize-space\([^)]*\))\s*,\s*["\'](.+?)["\']\s*\)',
        r'(?:text\(\)|normalize-space\([^)]*\))\s*=\s*["\'](.+?)["\']',
        r'\[aria-label\s*=\s*["\'](.+?)["\']\s*\]',
    ]
    for pat in patterns:
        m = re.search(pat, s)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


async def ensure_ephemeral_observer(target) -> None:
    """Ensures the ephemeral observer is installed on the target page/frame."""
    try:
        installed = await target.evaluate("() => Boolean(window.__flowEphemeralObserverInstalled)")
        if not installed:
            await target.evaluate(EPHEMERAL_MESSAGE_OBSERVER_SCRIPT)
    except Exception:
        pass


async def check_ephemeral_text(target, expected_text: str, exact: bool = False, window_ms: int = 25000) -> bool:
    """Checks whether expected_text was captured recently by the MutationObserver (across main page & iframes)."""
    norm_expected = _norm(expected_text)
    if not norm_expected:
        return False

    frames_to_check = []
    if hasattr(target, "frames"):
        frames_to_check.extend(target.frames)
    else:
        frames_to_check.append(target)

    for frame in frames_to_check:
        try:
            items = await frame.evaluate(
                "(ms) => window.__getFlowEphemeralList ? window.__getFlowEphemeralList(ms) : []",
                window_ms,
            )
            if not items:
                continue
            for item in items:
                norm_item = _norm(item)
                if exact:
                    if norm_item == norm_expected:
                        return True
                else:
                    if norm_expected in norm_item:
                        return True
        except Exception:
            continue
    return False
