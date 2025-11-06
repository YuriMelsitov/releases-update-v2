# 🤖 Автоматизация релизов в Confluence

Автоматическое обновление страницы Confluence на основе данных из Slack канала #npc_releases.

## Что делает

- ✅ Каждый день в 9:00 UTC проверяет релизы за 7 дней
- ✅ Извлекает: приложение, версию, дату, rollout %
- ✅ Обновляет Confluence автоматически

## Быстрая установка

### 1. Получить Slack токен (5 мин)

1. Открыть https://api.slack.com/apps
2. **Create New App** → **From scratch**
3. Название: `NPC Releases Bot`, workspace: `Appodeal`
4. **OAuth & Permissions** → **Bot Token Scopes**:
   - `channels:history`
   - `channels:read`
5. **Install to Workspace**
6. Скопировать **Bot User OAuth Token** (начинается с `xoxb-`)
7. В Slack: `/invite @NPC Releases Bot` в канале #npc_releases

### 2. Получить Atlassian токен (2 мин)

1. Открыть https://id.atlassian.com/manage-profile/security/api-tokens
2. **Create API token**
3. Название: `NPC Releases`
4. Скопировать токен

### 3. Создать GitHub репозиторий (5 мин)

1. На GitHub: **New repository**
2. Название: `npc-releases-automation`
3. Private
4. Не добавлять README

```bash
git init
git remote add origin git@github.com:USERNAME/npc-releases-automation.git
git add .
git commit -m "Initial commit"
git push -u origin main
```

### 4. Добавить секреты (5 мин)

Settings → Secrets and variables → Actions → New repository secret

| Название | Значение |
|----------|----------|
| `SLACK_TOKEN` | Токен из шага 1 |
| `ATLASSIAN_EMAIL` | `melsitov@appodeal.com` |
| `ATLASSIAN_API_TOKEN` | Токен из шага 2 |
| `ATLASSIAN_CLOUD_ID` | `6a51d52e-c04c-46db-8aa3-c4ca310eb3de` |
| `CONFLUENCE_PAGE_ID` | `6114246711` |

### 5. Запустить (2 мин)

1. Actions → **Обновление релизов в Confluence**
2. **Run workflow**
3. Проверить страницу!

## Настройка

### Изменить расписание

`.github/workflows/update-releases.yml`:
```yaml
schedule:
  - cron: '0 12 * * *'  # 12:00 UTC каждый день
  - cron: '0 9 * * 1'   # 9:00 UTC каждый понедельник
```

### Изменить период (не 7 дней)

`scripts/update_releases.py`, строка 25:
```python
timedelta(days=7)  # Поменять 7 на нужное число
```

## Результат

Страница обновляется автоматически с информацией:
- Название приложения
- Версия и билд
- Дата публикации
- Процент rollout
- Статус (Production, Testing, etc.)

## Ссылки

- **Confluence**: https://appodeal.atlassian.net/wiki/spaces/ChardonnayPartners/pages/6114246711/Playround
- **Slack**: https://appodeal.slack.com/archives/C033MFEDQ2C

## Проблемы?

- **"invalid_auth"** → Проверь Slack токен и scopes
- **"Page not found"** → Проверь Page ID: `6114246711`
- **"No releases"** → Возможно релизов не было за 7 дней

---

**Автор:** Yuri Melsitov  
**Email:** melsitov@appodeal.com
