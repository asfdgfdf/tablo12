# Табло: пальне ОККО і London Gas Oil

Публічна сторінка для Smart TV. Ціни ОККО та котирування ICE Gasoil оновлюються приблизно кожні 10 хвилин (GitHub Actions), сторінка сама перечитує дані щохвилини.

## Налаштування (≈10 хвилин, один раз)

1. **Акаунт.** Зареєструйтеся на github.com (або увійдіть).
2. **Репозиторій.** Натисніть «+» → *New repository*. Назва: `tablo`. Тип: **Public**. *Create repository*.
3. **Файли.** На сторінці репозиторію: *uploading an existing file* → перетягніть **увесь вміст** розпакованої папки `tablo` (`index.html`, `data.json`, `update.py`, `README.md`) → *Commit changes*.
   - Потім *Add file → Create new file*: у полі імені введіть `.github/workflows/update.yml`, вставте вміст файлу update.yml, *Commit changes*.
4. **Pages.** *Settings → Pages → Build and deployment → Source:* **GitHub Actions**.
5. **Права для оновлень.** *Settings → Actions → General → Workflow permissions:* **Read and write permissions** → *Save*.
6. **Перший запуск.** Вкладка *Actions* → «Оновлення табло» → *Run workflow*. Через 1–2 хвилини сайт буде доступний.

**Адреса для ТВ:** `https://<ваш-логін>.github.io/tablo/`

## Як це працює
- `update.py` завантажує okko.ua/fuels і investing.com, записує `data.json` (з історією за ~2 доби).
- `.github/workflows/update.yml` запускає його кожні 10 хв і публікує сайт на GitHub Pages.
- Якщо джерело недоступне, на табло лишаються останні цифри, а внизу з'являється жовте попередження.

## Корисно знати
- GitHub запускає розклад із затримкою 5–15 хв, тож фактичний інтервал 10–25 хв.
- Сторінка публічна: її бачить будь-хто з адресою (дані й так публічні).
- Зупинити оновлення: *Actions → Оновлення табло → … → Disable workflow*.
