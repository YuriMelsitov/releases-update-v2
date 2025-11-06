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
    
    def generate_confluence_html(self, releases):
        """Создать HTML контент для Confluence"""
        print("📝 Генерирую контент...")
        
        today = datetime.now().strftime('%d %B %Y')
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%d %B')
        
        html = f"<h1>NPC Releases - Последние 7 дней</h1>"
        html += f"<h2>Релизы за период {week_ago} - {today}</h2>"
        
        if not releases:
            html += "<p><em>Релизов за последние 7 дней не найдено.</em></p>"
        else:
            apps = {}
            for release in releases:
                app = release['app']
                if app not in apps:
                    apps[app] = []
                apps[app].append(release)
            
            for idx, (app_name, app_releases) in enumerate(apps.items(), 1):
                latest = app_releases[0]
                
                html += f"<h3>{idx}. {app_name}</h3>"
                html += "<ul>"
                html += f"<li><strong>Версия:</strong> {latest['version']}</li>"
                
                if latest['build']:
                    html += f"<li><strong>Build:</strong> {latest['build']}</li>"
                
                html += f"<li><strong>Дата публикации:</strong> {latest['date']} в {latest['time']}</li>"
                html += f"<li><strong>Rollout:</strong> {latest['rollout']}</li>"
                html += f"<li><strong>Статус:</strong> {latest['status']}</li>"
                html += "</ul>"
                
                if len(app_releases) > 1:
                    html += "<p><strong>История:</strong></p><ul>"
                    for rel in reversed(app_releases):
                        html += f"<li>{rel['date']} {rel['time']}: {rel['status']} ({rel['rollout']})</li>"
                    html += "</ul>"
                
                html += "<hr/>"
        
        html += "<h2>Процесс выкатки</h2>"
        html += "<p>Все релизы проходят следующие этапы:</p>"
        html += "<ol>"
        html += "<li>Внутреннее тестирование</li>"
        html += "<li>Проверка команд (SDK, Product, Monetization)</li>"
        html += "<li>Начальная выкатка 10%</li>"
        html += "<li>Проверка метрик (crash rates, ARPU, impressions)</li>"
        html += "<li>Увеличение до 20% при хороших показателях</li>"
        html += "<li>Постепенная выкатка до 100%</li>"
        html += "</ol>"
        html += "<hr/>"
        html += f"<p><em>Обновлено автоматически: {datetime.now().strftime('%d %B %Y в %H:%M UTC')}</em></p>"
        
        return html
    
    def update_confluence(self, content):
        """Обновить страницу в Confluence"""
        print("📤 Обновляю Confluence...")
        
        # Используем REST API v1 который поддерживает прямой HTML
        url = f"https://api.atlassian.com/ex/confluence/{self.cloud_id}/wiki/rest/api/content/{self.page_id}"
        auth = (self.atlassian_email, self.atlassian_token)
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        
        # Получаем текущую версию страницы
        response = requests.get(url, auth=auth, headers=headers)
        response.raise_for_status()
        current_page = response.json()
        current_version = current_page.get('version', {}).get('number', 1)
        title = current_page.get('title', 'NPC Releases')
        
        # Обновляем страницу
        payload = {
            'version': {
                'number': current_version + 1,
                'message': f'Автоматическое обновление {datetime.now().strftime("%Y-%m-%d %H:%M")}'
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
        
        response = requests.put(url, auth=auth, headers=headers, json=payload)
        
        if response.status_code != 200:
            print(f"Ошибка ответа: {response.status_code}")
            print(f"Тело ответа: {response.text}")
        
        response.raise_for_status()
        
        print(f"✅ Обновлено (v{current_version} → v{current_version + 1})")
    
    def run(self):
        """Основной метод"""
        print("🚀 Запуск...")
        
        try:
            # Получить сообщения из Slack
            messages = self.get_slack_messages()
            
            # Извлечь релизы
            releases = self.parse_releases(messages)
            
            # Показать найденные релизы
            if releases:
                print(f"\n📊 Найденные релизы:")
                for rel in releases[:5]:
                    print(f"  • {rel['app']} {rel['version']} - {rel['date']} ({rel['rollout']})")
                if len(releases) > 5:
                    print(f"  ... и еще {len(releases) - 5}")
            
            # Сгенерировать HTML контент
            content = self.generate_confluence_html(releases)
            
            # Обновить Confluence
            self.update_confluence(content)
            
            print("\n✅ Готово!")
            
        except Exception as e:
            print(f"\n❌ Ошибка: {str(e)}")
            import traceback
            traceback.print_exc()
            raise


if __name__ == '__main__':
    tracker = ReleaseTracker()
    tracker.run()
