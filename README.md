# VK-бот учёта посещаемости

Бот поддерживает несколько бюро. Главный администратор подтверждает руководителей, а каждый руководитель — сотрудников только своего бюро. После подтверждения сотрудник получает кнопки статусов; бот хранит одну актуальную отметку на день и формирует отдельный Excel для каждого бюро.

## Подготовка VK

1. Создайте или откройте сообщество VK.
2. Включите **Сообщения сообщества** и разрешите сообщения.
3. В **Управление → Работа с API → Long Poll API** включите Long Poll и событие «Входящее сообщение».
4. В **Ключи доступа** создайте ключ сообщества с правами на сообщения, управление и документы.
5. Узнайте числовой VK ID главного администратора. Все руководители должны первыми разрешить сообщения сообществу, иначе VK не даст отправить им заявки и отчёты.

## Запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Откройте `.env`, вставьте `VK_GROUP_TOKEN` и `SUPERADMIN_VK_ID`. Затем:

```bash
python bot.py
```

Для постоянной работы на Linux можно установить сервис:

```bash
sudo cp deploy/vk-attendance-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vk-attendance-bot
sudo systemctl status vk-attendance-bot
```

Перед копированием сервиса при необходимости поменяйте `User` и пути внутри файла.

## Запуск в контейнере Podman

Проект можно запускать в контейнере вместо systemd-сервиса. В `.env` должны быть заполнены `VK_GROUP_TOKEN` и `SUPERADMIN_VK_ID` (см. выше). Внутри контейнера база `attendance.sqlite3` и папка `generated_reports/` хранятся в `/app/data` — смонтируйте туда volume, чтобы данные не терялись при пересоздании контейнера.

Через `podman build`/`podman run`:

```bash
podman build -t vk-attendance-bot .
podman run -d \
  --name vk-attendance-bot \
  --restart unless-stopped \
  --env-file .env \
  -v vk-attendance-data:/app/data \
  vk-attendance-bot
```

Через `podman compose` (или `podman-compose`), используя `compose.yaml`:

```bash
podman compose up -d --build
```

Логи и статус:

```bash
podman logs -f vk-attendance-bot
podman ps
```

Чтобы контейнер запускался автоматически при загрузке системы через systemd (rootless), можно сгенерировать unit-файл:

```bash
podman generate systemd --new --files --name vk-attendance-bot
mkdir -p ~/.config/systemd/user
mv container-vk-attendance-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now container-vk-attendance-bot
loginctl enable-linger "$USER"
```

## Использование

Сотрудник пишет боту:

```text
Петров Пётр Сергеевич | 9433307 | 24-37
```

Номер бюро хранится как текст, поэтому допустимы значения с тире и буквами. Заявка уходит руководителю указанного бюро. Только после подтверждения сотрудник может отмечаться. Повторное нажатие в тот же день меняет статус. Дни задаются `WORKDAYS`; в выходной отметка не записывается.

Запрос роли руководителя:

```text
/руководитель Иванов Иван Иванович | 9433307 | 24-37
```

Главный администратор получает карточку и подтверждает или отклоняет заявку. Один руководитель отвечает за одно бюро.

Команды руководителя:

- `/р` — получить свежий Excel своего бюро;
- `/с` — список сотрудников своего бюро;
- `/б Иванов Иван Иванович` — изменить ФИО руководителя в Excel;
- `/к` — справка.

Каждому руководителю отправляется отдельный файл `День недели ДД.ММ.ГГГГ.xlsx`. Чтобы после тестов не получать файл при каждом нажатии, задайте `SEND_AFTER_EACH_MARK=false`. Ежедневная отправка в `REPORT_TIME` продолжит работать.

База хранится локально в `attendance.sqlite3`, готовые отчёты — в `generated_reports/`. Эти файлы не попадают в Git.
