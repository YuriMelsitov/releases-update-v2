#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Slack → Confluence release summary
- English output
- Reads channel + threads
- Robust app-name inference from replies
- Filters generic root titles ("Hi team", "New build is ready!", etc.)
"""

import os
import re
import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

class ReleaseTracker:
    # ---------- Slack cleanup ----------
    SLACK_LINK_RE = re.compile(r"<(https?://[^>|]+)\|([^>]+)>")
    SLACK_PLAIN_LINK_RE = re.compile(r"<(https?://[^>]+)>")
    SLACK_SUBTEAM_RE = re.compile(r"<!subteam\^[A-Z0-9]+(?:\|[^>]+)?>")
    SLACK_SPECIAL_RE = re.compile(r"<!(?:here|channel|everyone)>")
    SLACK_MENTION_RE = re.compile(r"<@([A-Z0-9]+)>")
    SLACK_EMOJI_RE = re.compile(r":[a-z0-9_+-]+:", re.IGNORECASE)
    HASH_TAG_RE = re.compile(r"(?:^|\s)#\S+")
    BULLET_DOT_RE = re.compile(r"^\s*[•\-\*]\s*")

    # ---------- Parsing regex ----------
    VERSION_ANY_RE = re.compile(r"\b(\d+\.\d+\.\d+)\b")
    ANNOUNCE_RE = re.compile(
        r"latest version of\s+(?P<name>.+?)\s*\(\s*(?P<version>\d+\.\d+\.\d+)\s*\)\s*is ready for rollout to\s*(?P<percent>\d+)%\s+of users on\s+(?P<platform>\w+)",
        re.IGNORECASE
    )
    NAME_LINE_RE = re.compile(r"^\s*(?P<name>[^:()]+?)(?:\s*\([^)]+\))?\s*$")
    INLINE_NAME_VERSION_RE = re.compile(
        r"^\s*(?P<name>.+?)\s*[-–:|]\s*Version[: ]\s*(?P<version>\d+\.\d+\.\d+)\b",
        re.IGNORECASE
    )
    ANY_VERSION_KV_RE = re.compile(r"^\s*Version\s*:\s*(?P<version>\d+\.\d+\.\d+)\b", re.IGNORECASE)
    ANY_BUILD_KV_RE = re.compile(r"^\s*Build\s*:\s*(?P<build>[\w#-]+)\b", re.IGNORECASE)
    ANY_PLATFORM_KV_RE = re.compile(r"^\s*Platform\s*:\s*(?P<platform>.+?)\s*$", re.IGNORECASE)
    ON_PLATFORM_RE = re.compile(r"\bon\s+(Android|iOS|iPadOS)\b", re.IGNORECASE)
    STATUS_KV_RE = re.compile(r"^\s*Status\s*:\s*(?P<status>.+?)\s*$", re.IGNORECASE)
    ROLLOUT_KV_RE = re.compile(r"^\s*(?:Rollout|Current Rollout)\s*:\s*(?P<rollout>.+?)\s*$", re.IGNORECASE)

    PLATFORM_VERSION_ROLLED_RE = re.compile(
        r"\b(?P<platform>Android|iOS|iPadOS)\b\s+(?P<version>\d+\.\d+\.\d+)\b.*?(?:rolled\s+out\s+to\s+(?P<pct>\d+)%)",
        re.IGNORECASE
    )
    PLATFORM_VERSION_LIVE_RE = re.compile(
        r"\b(?P<platform>Android|iOS|iPadOS)\b\s+(?P<version>\d+\.\d+\.\d+)\b.*?\bis\s+live\b",
        re.IGNORECASE
    )
    VERSION_ROLLED_RE = re.compile(
        r"\bVersion\s+(?P<version>\d+\.\d+\.\d+)\b.*?(?:Rolled\s+out\s+to\s+(?P<pct>\d+)%)",
        re.IGNORECASE
    )
    ROLLED_OUT_BANG_RE = re.compile(r"\b(\d+)%\s+rolled\s+out!?$", re.IGNORECASE)
    RELEASE_EMOJI_RE = re.compile(r":release[_\- ]?(\d+):", re.IGNORECASE)

    # Explicit app markers in replies
    APP_EXPLICIT_RE = re.compile(
        r"\b(?:app|application|game|project|title)\s*[:=\-]\s*(?P<name>[A-Za-z][A-Za-z0-9 +()\[\]&'./-]{2,60})\b",
        re.IGNORECASE
    )
    THIS_IS_RE = re.compile(
        r"\b(?:this\s+is|it\s+is|it\'s)\s+(?P<name>[A-Za-z][A-Za-z0-9 +()\[\]&'./-]{2,60})\b",
        re.IGNORECASE
    )
    SINGLE_BRACKETED_PLATFORM_RE = re.compile(r"\[(Android|iOS|iPadOS)\]", re.IGNORECASE)

    # ---------- Heuristics ----------
    STOPWORDS = {
        'thanks','thx','ok','okay','done','approved','approve','team','please','pls',
        'today','later','prod','production','build','ready','release','version',
        'push','green-light','greenlight','check','checked','qa','rollout','rolling',
        'status','published','platform','live','android','ios','ipados','workflow'
    }
    # titles that must NOT become app names
    GENERIC_TITLES_RE = re.compile(
        r"^(hi team|new build is ready!?|team[, ]|i take your check mark|please green-?light|pls green-?light)",
        re.IGNORECASE
    )

    ABBR_MAP = {'DMN':'Dominoes','NM':'Number Match','SPD':'Spades','BTK':'Block Tok'}
    CANONICAL_MAP = {
        'block tok':'Block Tok','dominoes':'Dominoes','number match':'Number Match',
        'spades':'Spades','klondike solitaire':'Klondike Solitaire','wordmaker':'Wordmaker'
    }
    APP_HINTS = [
        ("Dominoes", r"\bDominoes\b|\bDMN\b"),
        ("Number Match", r"\bNumber\s*Match\b|\bNM\b"),
        ("Spades", r"\bSpades\b|\bSPD\b"),
        ("Block Tok", r"\bBlock\s*Tok\b|\bBTK\b|\bBlock\s*tok\b"),
        ("Klondike Solitaire", r"\bKlondike(\s+Solitaire)?\b"),
        ("Wordmaker", r"\bWordmaker\b"),
    ]

    def __init__(self):
        self.slack_token = os.environ['SLACK_TOKEN']
        self.atlassian_email = os.environ['ATLASSIAN_EMAIL']
        self.atlassian_token = os.environ['ATLASSIAN_API_TOKEN']
        self.cloud_id = os.environ['ATLASSIAN_CLOUD_ID']
        self.page_id = os.environ['CONFLUENCE_PAGE_ID']
        self.channel_id = os.getenv('SLACK_CHANNEL_ID', 'C033MFEDQ2C')
        self.lookback_days = int(os.getenv('LOOKBACK_DAYS', '30'))

    # ---------- utils ----------
    @staticmethod
    def _clean_slack_text(text: str) -> str:
        if not text:
            return ""
        t = text
        t = ReleaseTracker.SLACK_LINK_RE.sub(lambda m: m.group(2), t)
        t = ReleaseTracker.SLACK_PLAIN_LINK_RE.sub(lambda m: m.group(1), t)
        t = ReleaseTracker.SLACK_SUBTEAM_RE.sub("", t)
        t = ReleaseTracker.SLACK_SPECIAL_RE.sub("", t)
        t = ReleaseTracker.SLACK_MENTION_RE.sub("", t)
        t = ReleaseTracker.SLACK_EMOJI_RE.sub("", t)
        t = t.replace("@here","").replace("@channel","")
        t = ReleaseTracker.HASH_TAG_RE.sub("", t)
        t = re.sub(r"\s+"," ", t).strip()
        return t

    @classmethod
    def _normalize_app_candidate(cls, name: str) -> Optional[str]:
        if not name: return None
        n = name.strip()
        n = re.sub(r"^[`*_~\s\[\]()+\-–—|:.,;]+|[`*_~\s\[\]()+\-–—|:.,;]+$", "", n)
        n = re.sub(r"\s*\[(Android|iOS|iPadOS)\]\s*$", "", n, flags=re.I).strip()
        if n.upper() in cls.ABBR_MAP: return cls.ABBR_MAP[n.upper()]
        m = re.match(r"^([A-Za-z][A-Za-z0-9 +&'./-]{2,60})\s*\(([A-Za-z0-9]{2,8})\)$", n)
        if m: n = m.group(1).strip()
        n = re.sub(r"\s+\b(app|application|game)\b\.?$","", n, flags=re.I).strip()
        low = n.lower()
        if low in cls.CANONICAL_MAP: n = cls.CANONICAL_MAP[low]
        if len(re.findall(r"[A-Za-z]", n)) < 2: return None
        if n.lower() in cls.STOPWORDS: return None
        if re.match(r"^(Build|APP|Application|Game|Approved|Approve|Status|Platform|Published|Workflow)\b", n, flags=re.I):
            return None
        if ReleaseTracker.GENERIC_TITLES_RE.match(n):  # key addition
            return None
        return n

    @classmethod
    def _infer_app_from_text(cls, text: str) -> str:
        if not text: return ""
        for canonical, pattern in cls.APP_HINTS:
            if re.search(pattern, text, flags=re.I):
                return canonical
        for abbr, full in cls.ABBR_MAP.items():
            if re.search(rf"\b{abbr}\b", text, flags=re.I):
                return full
        return ""

    @classmethod
    def _extract_explicit_app_from_text(cls, text: str) -> Optional[str]:
        if not text: return None
        mm = cls.APP_EXPLICIT_RE.search(text)
        if mm:
            n = cls._normalize_app_candidate(mm.group('name'))
            if n: return n
        mm2 = cls.THIS_IS_RE.search(text)
        if mm2:
            n = cls._normalize_app_candidate(mm2.group('name'))
            if n: return n
        line = text.strip()
        if "\n" not in line:
            n = cls._normalize_app_candidate(line)
            if n: return n
        return None

    # ---------- Slack API ----------
    def _slack_get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f'https://slack.com/api/{endpoint}'
        headers = {'Authorization': f'Bearer {self.slack_token}'}
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        data = resp.json()
        if not data.get('ok'):
            raise Exception(f"Slack API error ({endpoint}): {data.get('error')}")
        return data

    def get_slack_messages_with_threads(self) -> List[Dict[str, Any]]:
        print(f"📥 Fetching Slack messages + threads ({self.lookback_days} days)...")
        oldest_ts = (datetime.now(timezone.utc) - timedelta(days=self.lookback_days)).timestamp()
        all_msgs: List[Dict[str, Any]] = []
        cursor = None
        while True:
            params = {'channel': self.channel_id, 'oldest': oldest_ts, 'limit': 200}
            if cursor: params['cursor'] = cursor
            data = self._slack_get('conversations.history', params)
            msgs = data.get('messages', [])
            all_msgs.extend(msgs)
            cursor = (data.get('response_metadata') or {}).get('next_cursor')
            if not cursor: break

        for m in all_msgs:
            if m.get('thread_ts') and m.get('thread_ts') == m.get('ts'):
                rep_data = self._slack_get('conversations.replies', {
                    'channel': self.channel_id, 'ts': m['ts'], 'limit': 200
                })
                replies = rep_data.get('messages', [])
                m['_replies'] = replies[1:] if replies and replies[0].get('ts') == m.get('ts') else replies
        print(f"✅ Got {len(all_msgs)} messages")
        return all_msgs

    # ---------- Parse one message ----------
    def _parse_release_from_text(self, text: str) -> Dict[str, Any]:
        raw = text or ""
        s = self._clean_slack_text(raw)
        res: Dict[str, Any] = {
            'app': None,'version': None,'build': None,'platform': None,
            'status': None,'published': None,'rollout': None,
            'initial_rollout': None,'current_rollout': None,
            'key_changes': [],'timeline': [],
        }

        m = self.ANNOUNCE_RE.search(s)
        if m:
            res['app'] = m.group('name').strip()
            res['version'] = m.group('version').strip()
            res['platform'] = m.group('platform').strip()
            res['initial_rollout'] = f"{m.group('percent')}%"
            res['rollout'] = f"{m.group('percent')}% staged rollout"
            res['status'] = "Ready for rollout"
            return res

        for ln in s.splitlines():
            im = self.INLINE_NAME_VERSION_RE.match(ln.strip())
            if im:
                res['app'] = self._normalize_app_candidate(im.group('name').strip())
                res['version'] = im.group('version').strip()
                break

        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        for i, l in enumerate(lines):
            m_ver = self.ANY_VERSION_KV_RE.match(l)
            if m_ver: res['version'] = res['version'] or m_ver.group('version')
            m_build = self.ANY_BUILD_KV_RE.match(l)
            if m_build and not res.get('build'):
                res['build'] = self._clean_slack_text(m_build.group('build')).lstrip('#')
            m_pl = self.ANY_PLATFORM_KV_RE.match(l)
            if m_pl and not res.get('platform'):
                res['platform'] = self._clean_slack_text(m_pl.group('platform'))
            m_stat = self.STATUS_KV_RE.match(l)
            if m_stat: res['status'] = self._clean_slack_text(m_stat.group('status'))
            m_roll = self.ROLLOUT_KV_RE.match(l)
            if m_roll: res['current_rollout'] = self._clean_slack_text(m_roll.group('rollout'))

            if l.lower().startswith('recent changes') or l.lower().startswith('key changes'):
                j = i + 1
                while j < len(lines) and (self.BULLET_DOT_RE.match(lines[j]) or lines[j].startswith('•')):
                    item = self.BULLET_DOT_RE.sub('', lines[j]).strip()
                    if item: res['key_changes'].append(self._clean_slack_text(item))
                    j += 1

        if not res.get('platform'):
            mplat = self.ON_PLATFORM_RE.search(s)
            if mplat: res['platform'] = mplat.group(1)

        # HEAD line → candidate app (but reject generic titles!)
        if not res.get('app') and lines:
            head = self._clean_slack_text(lines[0])
            if not self.GENERIC_TITLES_RE.match(head):
                mname = self.NAME_LINE_RE.match(head)
                if mname and not re.match(r"^(Build|APP)\b", head, flags=re.I):
                    n = self._normalize_app_candidate(mname.group('name'))
                    if n: res['app'] = n

        # Status heuristics
        lower = s.lower()
        if not res.get('status'):
            if 'being rolled back' in lower or 'roll back' in lower:
                res['status'] = 'Being rolled back'
            elif 'in production' in lower or re.search(r'\bproduction\b', lower):
                res['status'] = 'In production'
            elif 'internal testing' in lower:
                res['status'] = 'Internal testing'
            elif 'rollout' in lower or 'rolled out' in lower:
                res['status'] = 'Staged rollout'
            elif 'ready' in lower:
                res['status'] = 'Ready for rollout'
            else:
                res['status'] = 'Unknown'

        if not res.get('app'):
            res['app'] = self._infer_app_from_text(s) or res.get('app')
        if not res.get('version'):
            mver = self.VERSION_ANY_RE.search(s)
            if mver: res['version'] = mver.group(1)
        return res

    # ---------- Merge replies ----------
    def _merge_replies_into_release(self, base: Dict[str, Any], replies: List[Dict[str, Any]]) -> Dict[str, Any]:
        joined_plain = []
        explicit_app: Optional[str] = None

        for r in replies or []:
            txt_raw = r.get('text') or ""
            txt = self._clean_slack_text(txt_raw)
            joined_plain.append(txt)

            # explicit/late app markers
            ea = self._extract_explicit_app_from_text(txt)
            if ea: explicit_app = ea  # keep latest to allow late "this is Block tok"

            m1 = self.PLATFORM_VERSION_ROLLED_RE.search(txt)
            if m1:
                base['platform'] = base.get('platform') or m1.group('platform')
                base['version'] = base.get('version') or m1.group('version')
                base['rollout'] = f"{m1.group('pct')}% staged rollout"
            m2 = self.PLATFORM_VERSION_LIVE_RE.search(txt)
            if m2:
                base['platform'] = base.get('platform') or m2.group('platform')
                base['version'] = base.get('version') or m2.group('version')
                base['status'] = "In production"
                base.setdefault('timeline', []).append("Live")
            m3 = self.VERSION_ROLLED_RE.search(txt)
            if m3 and not base.get('version'):
                base['version'] = m3.group('version')
                if m3.group('pct'): base['rollout'] = f"{m3.group('pct')}% staged rollout"

            b = self.ROLLED_OUT_BANG_RE.search(txt)
            if b:
                base['rollout'] = f"{b.group(1)}% staged rollout"
                base.setdefault('timeline', []).append(f"Rollout in progress ({b.group(1)}%)")
            e = self.RELEASE_EMOJI_RE.search(txt_raw)
            if e:
                base['rollout'] = f"{e.group(1)}% staged rollout"
                base.setdefault('timeline', []).append(f"Rollout in progress ({e.group(1)}%)")

            # build card fields
            if "Build Version:" in txt_raw or "Build Number:" in txt_raw or "Recent changes:" in txt_raw:
                lines = [l.strip() for l in txt_raw.splitlines()]
                for i, l in enumerate(lines):
                    if l.lower().startswith("build version"):
                        if i + 1 < len(lines) and not base.get('version'):
                            base['version'] = self._clean_slack_text(lines[i + 1])
                    if l.lower().startswith("build number"):
                        if i + 1 < len(lines):
                            base['build'] = self._clean_slack_text(lines[i + 1]).lstrip('#')
                    if l.lower().startswith("recent changes"):
                        j = i + 1
                        while j < len(lines) and (self.BULLET_DOT_RE.match(lines[j]) or lines[j].startswith('•')):
                            item = self.BULLET_DOT_RE.sub('', lines[j]).strip()
                            if item: base.setdefault('key_changes', []).append(self._clean_slack_text(item))
                            j += 1

            # timeline cues / approvals
            if re.search(r"\bchecked\b", txt, flags=re.I):
                base.setdefault('timeline', []).append("Checked by QA")
            if re.search(r"\bgreen\s*light(?:ed)?\b|\bgreen-?light(?:ed)?\b", txt, flags=re.I):
                base.setdefault('timeline', []).append("Green-lighted")
                base['status'] = base.get('status') or "Ready for rollout"
            if re.search(r"can be submitted to store|ready for submission", txt, flags=re.I):
                base.setdefault('timeline', []).append("Ready for submission")
                base['status'] = base.get('status') or "Ready for submission"

            mm = re.search(r"(Current Status|Current Rollout|Rollout|Status)\s*:\s*(.+)", txt_raw, flags=re.I)
            if mm:
                key = mm.group(1).lower()
                val = self._clean_slack_text(mm.group(2))
                if "status" in key: base['status'] = val
                else: base['current_rollout'] = val

            if not base.get('platform'):
                mplat = self.SINGLE_BRACKETED_PLATFORM_RE.search(txt)
                if mplat: base['platform'] = mplat.group(1)

        # if root 'app' was generic, replace with explicit/inferred
        def is_generic(name: Optional[str]) -> bool:
            return bool(name) and self.GENERIC_TITLES_RE.match(name)

        if explicit_app:
            if not base.get('app') or is_generic(base.get('app')):
                base['app'] = explicit_app
        if not base.get('app') or is_generic(base.get('app')):
            inferred = self._infer_app_from_text(" ".join(joined_plain))
            if inferred:
                base['app'] = inferred
            else:
                for line in joined_plain:
                    cand = self._extract_explicit_app_from_text(line)
                    if cand:
                        base['app'] = cand
                        break

        if not base.get('version'):
            mver = self.VERSION_ANY_RE.search(" ".join(joined_plain))
            if mver: base['version'] = mver.group(1)

        return base

    # ---------- Orchestrate ----------
    def parse_releases(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        print("🔍 Parsing releases...")
        releases: List[Dict[str, Any]] = []
        for msg in messages:
            text = msg.get('text', '') or ''
            ts = float(msg.get('ts', 0.0))
            root_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            is_thread_root = msg.get('thread_ts') and msg.get('thread_ts') == msg.get('ts')
            is_single = not msg.get('thread_ts')
            if not (is_thread_root or is_single): continue

            rel = self._parse_release_from_text(text)
            if not rel.get('published'):
                rel['published'] = root_dt.strftime("%B %d, %Y")
            if not rel.get('rollout'):
                m_pct = re.search(r'(\d+)%', text)
                if m_pct: rel['rollout'] = f"{m_pct.group(1)}% staged rollout"
            if is_thread_root and msg.get('_replies'):
                rel = self._merge_replies_into_release(rel, msg['_replies'])

            # require real app (drop generic or missing)
            if not rel.get('app') or self.GENERIC_TITLES_RE.match(rel['app']):
                continue

            if rel.get('version') or rel.get('status') or rel.get('key_changes'):
                rel['timestamp'] = ts
                releases.append(rel)

        # dedupe app-version
        unique: Dict[str, Dict[str, Any]] = {}
        for r in releases:
            key = f"{(r.get('app') or '').strip()}-{(r.get('version') or '').strip()}"
            if key not in unique or r['timestamp'] > unique[key]['timestamp']:
                unique[key] = r

        result = list(unique.values())
        result.sort(key=lambda x: x['timestamp'], reverse=True)
        print(f"✅ Found {len(result)} releases")
        return result

    # ---------- Render & update ----------
    @staticmethod
    def _li(key: str, val: str) -> str:
        return f"<li><strong>{key}:</strong> {val}</li>"

    def generate_confluence_html(self, releases: List[Dict[str, Any]]) -> str:
        print("📝 Generating Confluence...")
        now = datetime.now(timezone.utc)
        period_from = (now - timedelta(days=self.lookback_days)).strftime("%B %d")
        period_to = now.strftime("%B %d, %Y")
        html = f"<h1>NPC Releases — Last {self.lookback_days} days</h1>"
        html += f"<h2>Period: {period_from} – {period_to}</h2>"
        if not releases:
            html += "<p><em>No releases found in the selected period.</em></p>"
            return html

        apps: Dict[str, List[Dict[str, Any]]] = {}
        for r in releases:
            apps.setdefault(r['app'], []).append(r)

        for idx, app_name in enumerate(sorted(apps.keys()), 1):
            app_releases = apps[app_name]
            latest = app_releases[0]
            html += f"<h3>{idx}. {app_name}</h3><ul>"
            if latest.get('version'): html += self._li("Version", latest['version'])
            if latest.get('build'): html += self._li("Build", latest['build'])
            if latest.get('platform'): html += self._li("Platform", latest['platform'])
            if latest.get('published'): html += self._li("Published", latest['published'])
            if latest.get('rollout'): html += self._li("Rollout", latest['rollout'])
            if latest.get('status'): html += self._li("Status", latest['status'])
            html += "</ul>"
            if latest.get('key_changes'):
                html += "<p><strong>Key Changes:</strong></p><ul>"
                for ch in latest['key_changes'][:20]:
                    html += f"<li>{ch}</li>"
                html += "</ul>"
            if len(app_releases) > 1 or latest.get('timeline'):
                html += "<p><strong>Timeline:</strong></p><ul>"
                for t in latest.get('timeline', []):
                    html += f"<li>{t}</li>"
                for rel in reversed(app_releases[1:]):
                    pub = rel.get('published') or ''
                    st = rel.get('status') or 'Status update'
                    ro = rel.get('rollout') or ''
                    tail = f" ({ro})" if ro else ""
                    html += f"<li>{pub}: {st}{tail}</li>"
                html += "</ul>"
            html += "<hr/>"

        html += "<h2>Rollout Process</h2><p>All releases follow a staged rollout process:</p><ol>"
        html += "<li>Internal testing and verification</li>"
        html += "<li>Team approval (SDK, Product, Monetization teams)</li>"
        html += "<li>Initial 10% rollout</li>"
        html += "<li>Health checks (crash rates, ARPU, impressions per user)</li>"
        html += "<li>Increase to 20% if metrics are healthy</li>"
        html += "<li>Continue gradual rollout based on performance</li></ol><hr/>"
        html += f"<p><em>Auto-updated: {now.strftime('%B %d, %Y at %H:%M UTC')}</em></p>"
        return html

    def update_confluence(self, content: str):
        print("📤 Updating Confluence...")
        base = f"https://api.atlassian.com/ex/confluence/{self.cloud_id}/wiki"
        url = f"{base}/rest/api/content/{self.page_id}"
        auth = (self.atlassian_email, self.atlassian_token)
        headers = {'Accept':'application/json','Content-Type':'application/json'}
        r = requests.get(url, auth=auth, headers=headers, timeout=30)
        r.raise_for_status()
        current = r.json()
        current_version = (current.get('version') or {}).get('number', 1)
        title = current.get('title', 'NPC Releases')
        payload = {
            'version': {'number': current_version + 1,
                        'message': f'Automated update {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}'},
            'type': 'page',
            'title': title,
            'body': {'storage': {'value': content, 'representation': 'storage'}}
        }
        up = requests.put(url, auth=auth, headers=headers, json=payload, timeout=30)
        if up.status_code != 200:
            print(f"Confluence response status: {up.status_code}")
            print(f"Confluence response body: {up.text}")
        up.raise_for_status()
        print(f"✅ Updated (v{current_version} → v{current_version + 1})")

    def run(self):
        print("🚀 Run...")
        msgs = self.get_slack_messages_with_threads()
        rels = self.parse_releases(msgs)
        if rels:
            print("\n📊 Preview:")
            for r in rels[:10]:
                print(f"  • {(r.get('app') or 'Unknown')} {(r.get('version') or '')} — {(r.get('published') or '')}")
        html = self.generate_confluence_html(rels)
        self.update_confluence(html)
        print("\n✅ Done!")

if __name__ == '__main__':
    ReleaseTracker().run()
