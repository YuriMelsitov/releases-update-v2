#!/usr/bin/env python3
"""
Автоматическое обновление Confluence на основе релизов из Slack
"""

import os
import requests
from datetime import datetime, timedelta
import re


class ReleaseTracker:
    def __init__(self):
        # Читаем переменные окружения
        self.slack_token = os.environ['SLACK_TOKEN']
        self.atlassian_email = os.environ['ATLASSIAN_EMAIL']
        self.atlassian_token = os.environ['ATLASSIAN_API_TOKEN']
        self.cloud_id = os.environ['ATLASSIAN_CLOUD_ID']
        self.page_id = os.environ['CONFLUENCE_PAGE_ID']
        
        # ID канала #npc_releases
        self.channel_id = 'C033MFEDQ2C'
    
    def get_slack_messages(self):
        """Получить сообщения из Slack за последние 7 дней"""
        print("📥 Получаю сообщения из Slack...")
        
        seven_days_ago = (datetime.now() - timedelta(days=7)).timestamp()
        
        url = 'https://slack.com/api/conversations.history'
        headers = {'Authorization': f'Bearer {self.slack_token}'}
        params = {
            'channel': self.channel_id,
            'oldest': seven_days_ago,
            'limit': 200
        }
        
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        
        if not data.get('ok'):
            raise Exception(f"Ошибка Slack API: {data.get('error')}")
        
        messages = data.get('messages', [])
        print(f"✅ Получено {len(messages)} сообщений")
        return messages
    
    def parse_releases(self, messages):
        """Извлечь информацию о релизах из сообщений"""
        print("🔍 Анализирую релизы...")
        
        releases = []
        
        for msg in messages:
            text = msg.get('text', '')
            timestamp = float(msg.get('ts', 0))
            date = datetime.fromtimestamp(timestamp)
            
            # Ищем паттерны релизов
            version_match = re.search(r'(\w+(?:\s+\w+)*?)\s+(\d+\.\d+\.\d+)', text)
            build_match = re.search(r'Build\s+(\d+)', text, re.IGNORECASE)
            rollout_match = re.search(r'(\d+)%', text)
            
            # Определяем статус
            status = 'Unknown'
            if 'production' in text.lower():
                status = 'Production'
            elif 'internal testing' in text.lower():
                status = 'Internal Testing'
            elif 'rolled out' in text.lower() or 'rollout' in text.lower():
                status = 'Staged Rollout'
            elif 'ready' in text.lower():
                status = 'Ready for Rollout'
            
            if version_match:
                app_name = version_match.group(1).strip()
                version = version_match.group(2)
                
                # Очищаем название приложения
                app_name = re.sub(r'<!subteam\^[^>]+>', '', app_name).strip()
                
                release = {
                    'app': app_name,
                    'version': version,
                    'build': build_match.group(1) if build_match else None,
                    'rollout': rollout_match.group(1) + '%' if rollout_match else 'N/A',
                    'date': date.strftime('%Y-%m-%d'),
                    'time': date.strftime('%H:%M'),
                    'status': status,
                    'timestamp': timestamp
                }
                
                releases.append(release)
        
        # Убираем дубликаты
        unique_releases = {}
        for release in releases:
            key = f"{release['app']}-{release['version']}"
            if key not in unique_releases or release['timestamp'] > unique_releases[key]['timestamp']:
                unique_releases[key] = release
        
        releases = list(unique_releases.values())
        releases.sort(key=lambda x: x['timestamp'], reverse=True)
        
        print(f"✅ Найдено {len(releases)} релизов")
        return releases
    
    def generate_confluence_content(self, releases):
        """Создать контент для Confluence"""
        print("📝 Генерирую контент...")
        
        today = datetime.now().strftime('%d %B %Y')
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%d %B')
        
        content = f"""# NPC Releases - Последние 7 дней

## Релизы за период {week_ago} - {today}

"""
        
        if not releases:
            content += "_Релизов за последние 7 дней не найдено._\n\n"
        else:
            apps = {}
            for release in releases:
                app = release['app']
                if app not in apps:
                    apps[app] = []
                apps[app].append(release)
            
            for idx, (app_name, app_releases) in enumerate(apps.items(), 1):
                latest = app_releases[0]
                
                content += f"### {idx}. {app_name}\n\n"
                content += f"- **Версия:** {latest['version']}\n"
                
                if latest['build']:
                    content += f"- **Build:** {latest['build']}\n"
                
                content += f"- **Дата публикации:** {latest['date']} в {latest['time']}\n"
                content += f"- **Rollout:** {latest['rollout']}\n"
                content += f"- **Статус:** {latest['status']}\n"
                
                if len(app_releases) > 1:
                    content += "\n**История:**\n"
                    for rel in reversed(app_releases):
                        content += f"- {rel['date']} {rel['time']}: {rel['status']} ({rel['rollout']})\n"
                
                content += "\n---\n\n"
        
        content += f"""## Процесс выкатки

Все релизы проходят следующие этапы:

1. Внутреннее тестирование
2. Проверка команд (SDK, Product, Monetization)
3. Начальная выкатка 10%
4. Проверка метрик (crash rates, ARPU, impressions)
5. Увеличение до 20% при хороших показателях
6. Постепенная выкатка до 100%

---

*Обновлено автоматически: {datetime.now().strftime('%d %B %Y в %H:%M UTC')}*
"""
        
        return content
    
    def update_confluence(self, content):
        """Обновить страницу в Confluence"""
        print("📤 Обновляю Confluence...")
        
        url = f"https://api.atlassian.com/ex/confluence/{self.cloud_id}/wiki/api/v2/pages/{self.page_id}"
        auth = (self.atlassian_email, self.atlassian_token)
        headers = {'Content-Type': 'application/json'}
        
        # Получаем текущую версию
        response = requests.get(url, auth=auth, headers=headers)
        response.raise_for_status()
        current_page = response.json()
        current_version = current_page.get('version', {}).get('number', 1)
        
        # Обновляем
        payload = {
            'id': self.page_id,
            'status': 'current',
            'title': current_page.get('title'),
            'body': content,
            'version': {
                'number': current_version + 1,
                'message': f'Автоматическое обновление {datetime.now().strftime("%Y-%m-%d %H:%M")}'
            }
        }
        
        response = requests.put(url, auth=auth, headers=headers, json=payload)
        response.raise_for_status()
        
        print(f"✅ Обновлено (v{current_version} → v{current_version + 1})")
    
    def run(self):
        """Основной метод"""
        print("🚀 Запуск...")
        
        try:
            messages = self.get_slack_messages()
            releases = self.parse_releases(messages)
            
            if releases:
                print(f"\n📊 Найденные релизы:")
                for rel in releases[:5]:
                    print(f"  • {rel['app']} {rel['version']} - {rel['date']} ({rel['rollout']})")
                if len(releases) > 5:
                    print(f"  ... и еще {len(releases) - 5}")
            
            content = self.generate_confluence_content(releases)
            self.update_confluence(content)
            
            print("\n✅ Готово!")
            
        except Exception as e:
            print(f"\n❌ Ошибка: {str(e)}")
            raise


if __name__ == '__main__':
    tracker = ReleaseTracker()
    tracker.run()
