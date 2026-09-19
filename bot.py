from __future__ import annotations

import json
import logging
import random
import re
from datetime import datetime, time, timedelta

import vk_api
from apscheduler.schedulers.background import BackgroundScheduler
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from vk_api.keyboard import VkKeyboard, VkKeyboardColor

from config import Settings, load_settings
from database import Database, Employee, Manager
from report import build_report


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("attendance-bot")

STATUSES = {
    "present": "Пришёл на работу",
    "sick": "Больничный",
    "day_off": "Отгул",
    "unpaid": "Отпуск за свой счёт",
    "vacation": "Отпуск",
    "trip": "Командировка",
    "scheduled_absence": "Отсутствие по графику",
    "unknown": "Причина не выяснена",
}

REPORT_COMMANDS = {"/report", "отчёт", "отчет", "/р"}
EMPLOYEES_COMMANDS = {"/employees", "сотрудники", "/с"}
HELP_COMMANDS = {"начать", "/start", "помощь", "/help", "/к"}
NEXT_DAY_CUTOFF = time(12, 0)


def employee_keyboard() -> str:
    kb = VkKeyboard(one_time=False)
    buttons = [
        ("✅ На работе", "present", VkKeyboardColor.POSITIVE),
        ("🤒 Больничный", "sick", VkKeyboardColor.NEGATIVE),
        ("🏖 Отгул", "day_off", VkKeyboardColor.SECONDARY),
        ("💸 За свой счёт", "unpaid", VkKeyboardColor.SECONDARY),
        ("🌴 Отпуск", "vacation", VkKeyboardColor.PRIMARY),
        ("✈ Командировка", "trip", VkKeyboardColor.PRIMARY),
        ("📅 По графику", "scheduled_absence", VkKeyboardColor.SECONDARY),
        ("❓ Не выяснено", "unknown", VkKeyboardColor.NEGATIVE),
    ]
    for index, (label, status, color) in enumerate(buttons):
        # Обычная текстовая кнопка создаёт MESSAGE_NEW, которое Long Poll получает
        # без отдельной обработки события callback.
        kb.add_button(label, color=color, payload={"status": status})
        if index % 2 == 1 and index != len(buttons) - 1:
            kb.add_line()
    return kb.get_keyboard()


def approval_keyboard(request_vk_id: int, request_kind: str) -> str:
    kb = VkKeyboard(inline=True)
    kb.add_button(
        "✅ Подтвердить",
        color=VkKeyboardColor.POSITIVE,
        payload={"approval": "approve", "request_vk_id": request_vk_id, "request_kind": request_kind},
    )
    kb.add_button(
        "❌ Отклонить",
        color=VkKeyboardColor.NEGATIVE,
        payload={"approval": "reject", "request_vk_id": request_vk_id, "request_kind": request_kind},
    )
    return kb.get_keyboard()


def document_attachment(saved_document) -> str:
    """Приводит ответы docs.save разных версий VK API к строке вложения."""
    if isinstance(saved_document, list):
        if not saved_document:
            raise ValueError("VK не вернул загруженный документ")
        document = saved_document[0]
    elif isinstance(saved_document, dict):
        document = saved_document.get("doc", saved_document)
    else:
        raise ValueError("Неожиданный ответ VK при загрузке документа")
    try:
        return f"doc{document['owner_id']}_{document['id']}"
    except (KeyError, TypeError) as exc:
        raise ValueError("VK не вернул данные загруженного документа") from exc


def attendance_summary(attendance: dict[int, str]) -> tuple[int, int]:
    """Возвращает число реальных отметок и автоматически отмеченных отсутствий."""
    absent_without_mark = sum(status == "unknown" for status in attendance.values())
    return len(attendance) - absent_without_mark, absent_without_mark


def report_date_for_mark(now: datetime, workdays: frozenset[int]) -> datetime.date:
    """После полудня переносит отметку на ближайший следующий рабочий день."""
    if now.time() < NEXT_DAY_CUTOFF:
        return now.date()
    work_date = now.date() + timedelta(days=1)
    while work_date.isoweekday() not in workdays:
        work_date += timedelta(days=1)
    return work_date


class AttendanceBot:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.session = vk_api.VkApi(token=settings.vk_group_token, api_version="5.199")
        self.vk = self.session.get_api()
        group_response = self.vk.groups.getById()
        # VK менял оболочку ответа между версиями API.
        groups = group_response.get("groups", []) if isinstance(group_response, dict) else group_response
        self.group_id = settings.vk_group_id or int(groups[0]["id"])
        self.longpoll = VkBotLongPoll(self.session, self.group_id)
        self.upload = vk_api.VkUpload(self.session)
        self.keyboard = employee_keyboard()

    def send(self, user_id: int, text: str, *, keyboard: str | None = None, attachment: str | None = None) -> None:
        args = {"user_id": user_id, "message": text, "random_id": random.getrandbits(31)}
        if keyboard is not None:
            args["keyboard"] = keyboard
        if attachment:
            args["attachment"] = attachment
        self.vk.messages.send(**args)

    def send_employee_approval_request(self, employee: Employee) -> bool:
        """Отправляет заявку руководителю именно того бюро, которое указал сотрудник."""
        manager = self.db.manager_for_bureau(employee.bureau_number)
        if not manager:
            return False
        try:
            self.send(
                manager.vk_id,
                f"Новый сотрудник просит регистрацию:\n"
                f"{employee.full_name}\nТаб. № {employee.personnel_number}\nБюро: {employee.bureau_number}",
                keyboard=approval_keyboard(employee.vk_id, "employee"),
            )
        except vk_api.exceptions.ApiError as exc:
            log.warning("Не удалось доставить заявку сотрудника %s руководителю: %s", employee.vk_id, exc)
            return False
        return True

    def send_manager_approval_request(self, manager: Manager) -> bool:
        try:
            self.send(
                self.settings.superadmin_vk_id,
                f"Запрос на роль руководителя бюро:\n"
                f"{manager.full_name}\nТаб. № {manager.personnel_number}\nБюро: {manager.bureau_number}",
                keyboard=approval_keyboard(manager.vk_id, "manager"),
            )
        except vk_api.exceptions.ApiError as exc:
            log.warning("Не удалось доставить запрос руководителя %s администратору: %s", manager.vk_id, exc)
            return False
        return True

    def resend_pending_approvals(self) -> None:
        for manager in self.db.pending_managers():
            self.send_manager_approval_request(manager)
        for employee in self.db.pending_employees():
            self.send_employee_approval_request(employee)

    def today(self):
        return datetime.now(self.settings.timezone).date()

    def send_report(self, manager: Manager, work_date=None) -> None:
        work_date = work_date or self.today()
        employees = self.db.employees(manager.bureau_number)
        reports_dir = self.settings.reports_dir / re.sub(r"[^A-Za-zА-Яа-яЁё0-9_-]+", "_", manager.bureau_number)
        attendance = self.db.attendance(work_date, manager.bureau_number)
        path = build_report(
            self.settings.template_path,
            reports_dir,
            work_date,
            manager.bureau_number,
            manager.full_name,
            employees,
            attendance,
        )
        saved_document = self.upload.document_message(
            str(path), peer_id=manager.vk_id, title=path.name
        )
        attachment = document_attachment(saved_document)
        marked, absent_without_mark = attendance_summary(attendance)
        self.send(
            manager.vk_id,
            f"Отчёт бюро {manager.bureau_number} на {work_date.strftime('%d.%m.%Y')}: "
            f"отметились {marked} из {len(employees)}; без отметки {absent_without_mark}.",
            attachment=attachment,
        )

    def scheduled_report(self) -> None:
        now = datetime.now(self.settings.timezone)
        if now.isoweekday() not in self.settings.workdays:
            return
        self.db.mark_unmarked_as_unknown(now.date(), now)
        for manager in self.db.managers():
            try:
                self.send_report(manager, now.date())
            except Exception:
                log.exception("Не удалось отправить отчёт руководителю бюро %s", manager.bureau_number)

    def handle_manager(self, manager: Manager, text: str) -> bool:
        clean = text.strip()
        lower = clean.lower()
        boss_prefix = next((prefix for prefix in ("/boss ", "/б ") if lower.startswith(prefix)), None)
        if boss_prefix:
            name = clean[len(boss_prefix):].strip()
            if len(name.split()) < 2:
                self.send(manager.vk_id, "Укажите ФИО: /б Иванов Иван Иванович")
            else:
                self.db.set_manager_name(manager.vk_id, name)
                self.send(manager.vk_id, f"ФИО руководителя сохранено: {name}")
            return True
        if lower in REPORT_COMMANDS:
            self.send_report(manager)
            return True
        if lower in EMPLOYEES_COMMANDS:
            people = self.db.employees(manager.bureau_number)
            listing = "\n".join(
                f"{i}. {p.full_name} — {p.personnel_number}"
                for i, p in enumerate(people, 1)
            )
            self.send(manager.vk_id, listing or "Сотрудников пока нет.")
            return True
        if lower in HELP_COMMANDS:
            self.send(
                manager.vk_id,
                f"Вы руководитель бюро {manager.bureau_number}.\n"
                "/р — получить Excel сейчас\n"
                "/с — список сотрудников\n"
                "/б Фамилия Имя Отчество — изменить ФИО в отчёте\n"
                "/к — показать эту справку",
            )
            return True
        return False

    def handle_manager_request(self, user_id: int, text: str) -> bool:
        match = re.fullmatch(r"\s*/(?:руководитель|manager)\s+(.+?)\s*", text, flags=re.IGNORECASE)
        if not match:
            return False
        existing = self.db.manager(user_id)
        if existing and existing.active and existing.approved:
            self.send(user_id, f"Вы уже руководитель бюро {existing.bureau_number}.")
            return True
        details = re.fullmatch(
            r"\s*(.+?)\s*[|;]\s*([A-Za-zА-Яа-яЁё0-9_-]+)\s*[|;]\s*([^|;]+?)\s*",
            match.group(1),
        )
        if not details:
            self.send(
                user_id,
                "Для запроса роли руководителя отправьте:\n"
                "/руководитель Фамилия Имя Отчество | табельный номер | номер бюро",
            )
            return True
        full_name, personnel_number, bureau_number = details.groups()
        if len(full_name.split()) < 2:
            self.send(user_id, "Укажите хотя бы фамилию и имя руководителя.")
            return True
        manager = self.db.request_manager(
            user_id, full_name.strip(), personnel_number.strip(), bureau_number.strip()
        )
        self.send(
            user_id,
            f"Запрос на роль руководителя бюро {manager.bureau_number} отправлен главному администратору.",
        )
        self.send_manager_approval_request(manager)
        return True

    def handle_message(self, user_id: int, text: str, payload_raw) -> None:
        payload = {}
        if payload_raw:
            try:
                payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
            except (TypeError, json.JSONDecodeError):
                pass

        if payload.get("approval") in {"approve", "reject"}:
            try:
                request_vk_id = int(payload.get("request_vk_id", payload.get("employee_vk_id")))
            except (KeyError, TypeError, ValueError):
                self.send(user_id, "Не удалось определить заявку.")
                return
            if payload.get("request_kind", "employee") == "manager":
                if user_id != self.settings.superadmin_vk_id:
                    return
                candidate = self.db.manager(request_vk_id)
                if not candidate or not candidate.active or candidate.approved:
                    self.send(user_id, "Эта заявка руководителя уже была обработана.")
                    return
                if payload["approval"] == "approve":
                    try:
                        manager = self.db.approve_manager(request_vk_id)
                    except ValueError as exc:
                        self.send(user_id, str(exc))
                        return
                    self.send(user_id, f"Руководитель подтверждён: {manager.full_name}, бюро {manager.bureau_number}.")
                    self.send(
                        request_vk_id,
                        f"Вы назначены руководителем бюро {manager.bureau_number}. Теперь можно подтверждать сотрудников.",
                    )
                    for employee in self.db.pending_employees(manager.bureau_number):
                        self.send_employee_approval_request(employee)
                else:
                    self.db.reject_manager(request_vk_id)
                    self.send(user_id, f"Запрос руководителя отклонён: {candidate.full_name}.")
                    self.send(request_vk_id, "Главный администратор отклонил запрос на роль руководителя.")
                return

            manager = self.db.manager(user_id)
            employee = self.db.employee(request_vk_id)
            if not manager or not manager.active or not manager.approved:
                return
            if not employee or not employee.active or employee.approved:
                self.send(user_id, "Эта заявка сотрудника уже была обработана.")
                return
            if employee.bureau_number != manager.bureau_number:
                self.send(user_id, "Можно подтверждать только сотрудников своего бюро.")
                return
            if payload["approval"] == "approve":
                self.db.approve(request_vk_id)
                self.send(user_id, f"Сотрудник подтверждён: {employee.full_name}.")
                self.send(request_vk_id, "Руководитель подтвердил регистрацию. Теперь можно отмечаться.", keyboard=self.keyboard)
            else:
                self.db.reject(request_vk_id)
                self.send(user_id, f"Заявка отклонена: {employee.full_name}.")
                self.send(request_vk_id, "Руководитель отклонил регистрацию. Проверьте данные и зарегистрируйтесь заново.")
            return

        if user_id == self.settings.superadmin_vk_id and text.strip().lower() in HELP_COMMANDS:
            self.send(
                user_id,
                "Вы главный администратор. Запросы на роль руководителя приходят отдельными карточками.",
            )
            return

        manager = self.db.manager(user_id)
        if manager and manager.active and manager.approved and self.handle_manager(manager, text):
            return

        if self.handle_manager_request(user_id, text):
            return

        employee = self.db.employee(user_id)
        status = payload.get("status")

        if employee and employee.active and employee.approved and status in STATUSES:
            now = datetime.now(self.settings.timezone)
            if now.isoweekday() not in self.settings.workdays and now.time() < NEXT_DAY_CUTOFF:
                self.send(user_id, "Сегодня не настроен рабочий день. Отметка не записана.", keyboard=self.keyboard)
                return
            work_date = report_date_for_mark(now, self.settings.workdays)
            self.db.mark(user_id, work_date, status, now)
            if work_date == now.date():
                response = f"Записано: {STATUSES[status]}. Вы можете изменить отметку этой же кнопкой."
            else:
                response = (
                    f"Записано на {work_date.strftime('%d.%m.%Y')}: {STATUSES[status]}. "
                    "Вы можете изменить отметку этой же кнопкой."
                )
            self.send(user_id, response, keyboard=self.keyboard)
            if self.settings.send_after_each_mark and work_date == now.date():
                try:
                    manager = self.db.manager_for_bureau(employee.bureau_number)
                    if manager:
                        self.send_report(manager, work_date)
                except Exception:
                    log.exception("Не удалось отправить тестовый отчёт руководителю")
            return

        if employee and employee.active and employee.approved:
            self.send(user_id, f"{employee.full_name}, выберите сегодняшний статус:", keyboard=self.keyboard)
            return

        if employee and employee.active and not employee.approved:
            if self.send_employee_approval_request(employee):
                self.send(user_id, "Регистрация отправлена руководителю. Дождитесь подтверждения.")
            else:
                self.send(user_id, "Заявка сохранена. Для этого бюро пока не назначен руководитель.")
            return

        match = re.fullmatch(
            r"\s*(.+?)\s*[|;]\s*([A-Za-zА-Яа-яЁё0-9_-]+)\s*[|;]\s*([^|;]+?)\s*",
            text,
        )
        if not match:
            self.send(
                user_id,
                "Для регистрации отправьте одной строкой:\n"
                "Фамилия Имя Отчество | табельный номер | номер бюро\n\n"
                "Пример: Петров Пётр Сергеевич | 9433307 | 24-37",
            )
            return
        full_name, personnel_number, bureau_number = match.groups()
        if len(full_name.split()) < 2:
            self.send(user_id, "Укажите хотя бы фамилию и имя, затем | и табельный номер.")
            return
        try:
            employee = self.db.register(
                user_id, full_name.strip(), personnel_number.strip(), bureau_number.strip()
            )
        except ValueError as exc:
            self.send(user_id, str(exc))
            return
        self.send(
            user_id,
            f"Заявка принята: {employee.full_name}, таб. № {employee.personnel_number}, "
            f"бюро {employee.bureau_number}. Дождитесь подтверждения.",
        )
        if not self.send_employee_approval_request(employee):
            self.send(user_id, "Для этого бюро пока не назначен руководитель.")

    def run(self) -> None:
        scheduler = BackgroundScheduler(timezone=self.settings.timezone)
        scheduler.add_job(
            self.scheduled_report,
            "cron",
            hour=self.settings.report_time.hour,
            minute=self.settings.report_time.minute,
            id="daily_report",
            replace_existing=True,
        )
        scheduler.start()
        log.info("Бот запущен для сообщества %s", self.group_id)
        self.resend_pending_approvals()
        try:
            for event in self.longpoll.listen():
                if event.type == VkBotEventType.MESSAGE_NEW:
                    message = event.object.message
                    self.handle_message(message["from_id"], message.get("text", ""), message.get("payload"))
        finally:
            scheduler.shutdown(wait=False)


if __name__ == "__main__":
    AttendanceBot(load_settings()).run()
