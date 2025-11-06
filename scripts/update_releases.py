#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Automatic Confluence update from Slack release posts (last 7 days)
- English output
- Analyzes messages AND threads
- Parses announcements (name, version, platform, rollout), build cards, and timeline cues
"""

import os
import requests
from datetime import datetime, timedelta, timezone
import re
from typing import List, Dict, Any


class ReleaseTracker:
    # ---------- Regex helpers ----------
    SLACK_LINK_RE = re.compile(r"<(https?://[^>|]+)\|([^>]+)>")
    SLACK_PLAIN_LINK_RE = re.compile(r"<(https?://[^>]+)>")
    SLACK_SUBTEAM_RE = re.compile(r"<!subteam\^[A-Z0-9]+(?:\|[^>]+)?>")
    SLACK_SPECIAL_RE = re.compile(r"<!(?:here|channel|everyone)>")
    SLACK_MENTION_RE = re.compile(r"<@([A-Z0-9]+)>")
    HASH_TAG_RE = re.compile(r"(?:^|\s)#\S+")
    BULLET_DOT_RE = re.compile(r"^\s*[•\-\*]\s*")
    # "10% → 20%" or "10% -> 20%"
    PERCENT_ARROW_RE = re.compile(r"(\d+%)\s*(?:→|->)\s*(\d+%)")
    # “latest version of Klondike Solitaire (5.0.1) is ready for rollout to 10% of users on Android”
    ANNOUNCE_RE = re.compile(
        r"latest version of\s+(?P<name>.+?)\s*\(\s*(?P<version>\d+\.\d+\.\d+)\s*\)\s*is ready for rollout to\s*(?P<percent>\d+)%\s+of users on\s+(?P<platform>\w+)",
        re.IGNORECASE
    )

    def __init__(self):
        # --- env ---
        self.slack_token = os.environ['SLACK_TOKEN']  # xoxb-...
        self.atlassian_email = os.environ['ATLASSIAN_EMAIL']
        self.atlassian_token = os.environ['ATLASSIAN_API_TOKEN']
        self.cloud_id = os.environ['ATLASSIAN_CLOUD_ID']
        self.page_id = os.environ['CONFLUENCE_PAGE_ID']

        # Slack channel id (your #npc_releases)
        self.channel_id = 'C033MFEDQ2C'

    # ---------- Slack helpers ----------
    @staticmethod
    def _clean_slack_text(text: str) -> str:
        """Convert Slack markup to plain text and remove noise."""
        if not text:
            return ""
        t = text
        t = ReleaseTracker.SLACK_LINK_RE.sub(lambda m: m.group(2), t)        # <url|label> -> label
        t = ReleaseTracker.SLACK_PLAIN_LINK_RE.sub(lambda m: m.group(1), t)  # <url> -> url
        t = ReleaseTracker.SLACK_SUBTEAM_RE.sub("", t)                       # <!subteam^...>
        t = ReleaseTracker.SLACK_SPECIAL_RE.sub("", t)                       # <!here>, <!channel>, <!everyone>
        t = ReleaseTracker.SLACK_MENTION_RE.sub("", t)                       # <@U123> -> ""
        t = t.replace("@here", "").replace("@channel", "")
        t = ReleaseTracker.HASH_TAG_RE.sub("", t)                            # remove #tags like #npc_releases
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _slack_get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f'https://slack.com/api/{endpoint}'
        headers = {'Authorization': f'Bearer {self.slack_token}'}
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        data = resp.json()
        if not data.get('ok'):
            raise Exception(f"Slack API error ({endpoint}): {data.get('error')}")
        return data

    def get_slack_messages_with_threads(self) -> List[Dict[str, Any]]:
        """Fetch last 7 days of channel messages; attach replies to thread roots."""
        print("📥 Fetching Slack messages + threads (7 days)...")
        oldest_ts = (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()

        # channel history (paginate)
        all_msgs: List[Dict[str, Any]] = []
        cursor = None
        while True:
            params = {
                'channel': self.channel_id,
                'oldest': oldest_ts,
                'limit': 200
            }
            if cursor:
                params['cursor'] = cursor
            data = self._slack_get('conversations.history', params)
            msgs = data.get('messages', [])
            all_msgs.extend(msgs)
            cursor = (data.get('response_metadata') or {}).get('next_cursor')
            if not cursor:
                break

        # Attach replies for thread roots
        for m in all_msgs:
            if m.get('thread_ts') and m.get('thread_ts') == m.get('ts'):
                # thread root
                rep_data = self._slack_get('conversations.replies', {
                    'channel': self.channel_id,
                    'ts': m['ts'],
                    'limit': 200
                })
                replies = rep_data.get('messages', [])
                # store without duplicating root (root is first in replies)
                m['_replies'] = replies[1:] if replies and replies[0].get('ts') == m.get('ts') else replies

        print(f"✅ Got {len(all_msgs)} messages")
        return all_msgs

    # ---------- Parsing ----------
    def _parse_release_from_text(self, text: str) -> Dict[str, Any]:
        """Parse a single message into release dict (announcement/build card fallback)."""
        raw = text or ""
        s = self._clean_slack_text(raw)
        res = {
            'app': None,
            'version': None,
            'build': None,
            'platform': None,
            'status': None,
            'published': None,
            'rollout': None,
            'initial_rollout': None,
            'current_rollout': None,
            'key_changes': [],
            'timeline': [],
        }

        # Announcement
        m = self.ANNOUNCE_RE.search(s)
        if m:
            res['app'] = m.group('name').strip()
            res['version'] = m.group('version').strip()
            res['platform'] = m.group('platform').strip()
            res['initial_rollout'] = f"{m.group('percent')}%"
            res['rollout'] = f"{m.group('percent')}% staged rollout"
            res['status'] = "Ready for rollout"
            return res

        # Build card fallback: “Build Version:”, “Build Number:”, “Recent changes:”
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        for i, l in enumerate(lines):
            if l.lower().startswith('build version'):
                if i + 1 < len(lines):
                    res['version'] = self._clean_slack_text(lines[i + 1])
            if l.lower().startswith('build number'):
                if i + 1 < len(lines):
                    res['build'] = self._clean_slack_text(lines[i + 1]).lstrip('#')
            if l.lower().startswith('recent changes') or l.lower().startswith('key changes'):
                j = i + 1
                while j < len(lines) and (self.BULLET_DOT_RE.match(lines[j]) or lines[j].startswith('•')):
                    item = self.BULLET_DOT_RE.sub('', lines[j]).strip()
                    if item:
                        res['key_changes'].append(self._clean_slack_text(item))
                    j += 1

        # Try simple “AppName x.y.z” from line
        if not res['app'] and lines:
            head = self._clean_slack_text(lines[0])
            m2 = re.search(r"^(.+?)\s*\((\d+\.\d+\.\d+)\)$", head)
            if m2:
                res['app'] = m2.group(1).strip()
                res['version'] = res['version'] or m2.group(2).strip()
        return res

    def _merge_replies_into_release(self, base: Dict[str, Any], replies: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Enrich release with info parsed from thread replies."""
        for r in replies or []:
            txt_raw = r.get('text') or ""
            txt = self._clean_slack_text(txt_raw)

            # Build card enrichment
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
                            if item:
                                base.setdefault('key_changes', []).append(self._clean_slack_text(item))
                            j += 1

            # Timeline cues
            if re.search(r"\bchecked\b", txt, flags=re.I):
                base.setdefault('timeline', []).append("Checked by QA")
            if re.search(r"Rolling out the new version", txt, flags=re.I):
                base['status'] = "Rollout in progress"
                base.setdefault('timeline', []).append("Rollout started")

            # Explicit status/rollout updates
            mm = re.search(r"(Current Status|Current Rollout|Rollout|Status)\s*:\s*(.+)", txt_raw, flags=re.I)
            if mm:
                key = mm.group(1).lower()
                val = self._clean_slack_text(mm.group(2))
                if "status" in key:
                    base['status'] = val
                else:
                    base['current_rollout'] = val
        return base

    def parse_releases(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract release info from messages and their threads; returns latest per app+version."""
        print("🔍 Parsing releases from messages + threads...")
        releases = []

        for msg in messages:
            text = msg.get('text', '') or ''
            ts = float(msg.get('ts', 0.0))
            root_dt = datetime.fromtimestamp(ts, tz=timezone.utc)

            # Only consider thread roots and single posts (non-reply)
            is_thread_root = msg.get('thread_ts') and msg.get('thread_ts') == msg.get('ts')
            is_single = not msg.get('thread_ts')

            if not (is_thread_root or is_single):
                continue

            rel = self._parse_release_from_text(text)
            # Published date from root message
            if not rel.get('published'):
                rel['published'] = root_dt.strftime("%B %d, %Y")
            # Rollout basic guess
            if not rel.get('rollout'):
                m_pct = re.search(r'(\d+)%', text)
                if m_pct:
                    rel['rollout'] = f"{m_pct.group(1)}% staged rollout"

            # Status heuristic (English)
            lower = text.lower()
            if not rel.get('status'):
                if 'in production' in lower or 'production' in lower:
                    rel['status'] = 'In production'
                elif 'internal testing' in lower:
                    rel['status'] = 'Internal testing'
                elif 'rolled out' in lower or 'rollout' in lower:
                    rel['status'] = 'Staged rollout'
                elif 'ready' in lower:
                    rel['status'] = 'Ready for rollout'
                else:
                    rel['status'] = 'Unknown'

            # Merge thread replies
            if is_thread_root and msg.get('_replies'):
                rel = self._merge_replies_into_release(rel, msg['_replies'])

            # Keep only reasonable releases
            if rel.get('app') or rel.get('version') or rel.get('key_changes'):
                rel['timestamp'] = ts
                releases.append(rel)

        # Deduplicate: keep the latest per app-version
        unique: Dict[str, Dict[str, Any]] = {}
        for r in releases:
            key = f"{(r.get('app') or '').strip()}-{(r.get('version') or '').strip()}"
            if key not in unique or r['timestamp'] > unique[key]['timestamp']:
                unique[key] = r

        result = list(unique.values())
        result.sort(key=lambda x: x['timestamp'], reverse=True)
        print(f"✅ Found {len(result)} releases")
        return result

    # ---------- Confluence rendering (EN) ----------
    @staticmethod
    def _li(key: str, val: str) -> str:
        return f"<li><strong>{key}:</strong> {val}</li>"

    def generate_confluence_html(self, releases: List[Dict[str, Any]]) -> str:
        """Create Confluence Storage HTML in English."""
        print("📝 Generating Confluence content (EN)...")
        now = datetime.now(timezone.utc)
        period_from = (now - timedelta(days=7)).strftime("%B %d")
        period_to = now.strftime("%B %d, %Y")

        html = f"<h1>NPC Releases — Last 7 days</h1>"
        html += f"<h2>Period: {period_from} – {period_to}</h2>"

        if not releases:
            html += "<p><em>No releases found in the last 7 days.</em></p>"
        else:
            # group by app
            apps: Dict[str, List[Dict[str, Any]]] = {}
            for r in releases:
                app = r.get('app') or 'Unknown App'
                apps.setdefault(app, []).append(r)

            for idx, (app_name, app_releases) in enumerate(apps.items(), 1):
                latest = app_releases[0]
                html += f"<h3>{idx}. {app_name}</h3><ul>"

                if latest.get('version'):
                    html += self._li("Version", latest['version'])
                if latest.get('build'):
                    html += self._li("Build", latest['build'])
                if latest.get('platform'):
                    html += self._li("Platform", latest['platform'])
                if latest.get('published'):
                    html += self._li("Published", latest['published'])
                if latest.get('rollout'):
                    html += self._li("Rollout", latest['rollout'])
                if latest.get('status'):
                    html += self._li("Status", latest['status'])
                html += "</ul>"

                if latest.get('key_changes'):
                    html += "<p><strong>Key Changes:</strong></p><ul>"
                    for ch in latest['key_changes'][:20]:
                        html += f"<li>{ch}</li>"
                    html += "</ul>"

                if len(app_releases) > 1 or latest.get('timeline'):
                    html += "<p><strong>Timeline:</strong></p><ul>"
                    # thread timeline
                    for t in latest.get('timeline', []):
                        html += f"<li>{t}</li>"
                    # historical statuses for this app (from other messages)
                    for rel in reversed(app_releases[1:]):
                        pub = rel.get('published') or ''
                        st = rel.get('status') or 'Status update'
                        ro = rel.get('rollout') or ''
                        tail = f" ({ro})" if ro else ""
                        html += f"<li>{pub}: {st}{tail}</li>"
                    html += "</ul>"

                html += "<hr/>"

        html += "<h2>Rollout Process</h2>"
        html += "<p>All releases follow a staged rollout process:</p>"
        html += "<ol>"
        html += "<li>Internal testing and verification</li>"
        html += "<li>Team approval (SDK, Product, Monetization teams)</li>"
        html += "<li>Initial 10% rollout</li>"
        html += "<li>Health checks (crash rates, ARPU, impressions per user)</li>"
        html += "<li>Increase to 20% if metrics are healthy</li>"
        html += "<li>Continue gradual rollout based on performance</li>"
        html += "</ol>"
        html += "<hr/>"
        html += f"<p><em>Auto-updated: {now.strftime('%B %d, %Y at %H:%M UTC')}</em></p>"

        return html

    # ---------- Confluence update ----------
    def update_confluence(self, content: str):
        """Update page using Atlassian Cloud REST (storage format)."""
        print("📤 Updating Confluence page...")
        base = f"https://api.atlassian.com/ex/confluence/{self.cloud_id}/wiki"
        url = f"{base}/rest/api/content/{self.page_id}"
        auth = (self.atlassian_email, self.atlassian_token)
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}

        # Get current version/title
        r = requests.get(url, auth=auth, headers=headers, timeout=30)
        r.raise_for_status()
        current = r.json()
        current_version = (current.get('version') or {}).get('number', 1)
        title = current.get('title', 'NPC Releases')

        payload = {
            'version': {
                'number': current_version + 1,
                'message': f'Automated update {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}'
            },
            'type': 'page',
            'title': title,
            'body': {
                'storage': {
                    'value': content,
                    'representation': 'storage'
                }
            }
        }
        up = requests.put(url, auth=auth, headers=headers, json=payload, timeout=30)
        if up.status_code != 200:
            print(f"Confluence response status: {up.status_code}")
            print(f"Confluence response body: {up.text}")
        up.raise_for_status()
        print(f"✅ Updated (v{current_version} → v{current_version + 1})")

    # ---------- Main ----------
    def run(self):
        print("🚀 Run...")
        try:
            messages = self.get_slack_messages_with_threads()
            releases = self.parse_releases(messages)

            # small console preview
            if releases:
                print("\n📊 Releases:")
                for rel in releases[:5]:
                    app = rel.get('app') or 'Unknown'
                    ver = rel.get('version') or '?'
                    ro = rel.get('rollout') or 'N/A'
                    pub = rel.get('published') or ''
                    print(f"  • {app} {ver} — {pub} ({ro})")
                if len(releases) > 5:
                    print(f"  ... and {len(releases) - 5} more")

            html = self.generate_confluence_html(releases)
            self.update_confluence(html)
            print("\n✅ Done!")

        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
            raise


if __name__ == '__main__':
    tracker = ReleaseTracker()
    tracker.run()
