"""Сервис диагностики: оценка правил, гистерезис, дедупликация, API-снимок."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from .. import analytics, cv_state, db
from ..config import get_settings
from ..visual_signs import heuristic_sign_for_class, sign_definition
from ..vision.constants import VISUAL_SIGNS_RULE_ID, VISUAL_SIGNS_TITLE
from .catalog import (
    SEVERITY_RANK,
    Rule,
    RuleCatalog,
    catalog_as_dict,
    load_catalog,
)
from .evaluator import (
    VISUAL_DEFECT_CLASSES,
    RuleEvaluation,
    dedupe,
    evaluate_all,
    sort_evaluations,
)


def _now(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _now(datetime.fromisoformat(value))
    except ValueError:
        return None


def _with_ocr(
    points: Sequence[tuple[datetime, dict[str, Any]]],
    cv: Any,
    since: datetime | None = None,
) -> list[tuple[datetime, dict[str, Any]]]:
    """Подставляет OCR-значение в последний сэмпл телеметрии.

    Приоритет источников:
    1. `manometer_ocr` в самом свежем сэмпле (сценарий симулятора `gauge_drift`);
    2. `manometer_ocr` в пределах `CV_TTL_SEC` — если сигнал приходит реже
       телеметрии или пишут несколько процессов;
    3. свежее OCR-значение с камеры из `cv_state` (`/ws/frame`).

    `since` — начало текущего сценария: значения из прошлого сценария не
    используются, иначе правило держалось бы ещё TTL секунд после reset.
    """
    ordered = list(points)
    if not ordered:
        return ordered
    moment, signals = ordered[-1]
    if signals.get("manometer_ocr") is not None:
        return ordered

    ttl = get_settings().cv_ttl_sec
    cutoff = moment - timedelta(seconds=ttl)
    if since is not None:
        # `scenario_state.started_at` отдаётся с точностью до секунды (db.iso),
        # поэтому поднимаем границу на секунду: иначе сэмпл, записанный в
        # последнюю секунду прошлого сценария, «просочится» в новый навсегда.
        since = since.replace(microsecond=0) + timedelta(seconds=1)
        cutoff = max(cutoff, since)
    for timestamp, previous in reversed(ordered):
        if timestamp < cutoff:
            break
        value = previous.get("manometer_ocr")
        if value is not None:
            ordered[-1] = (moment, {**signals, "manometer_ocr": value})
            return ordered

    ocr = getattr(cv, "ocr", None)
    if not ocr:
        return ordered
    value = ocr.get("value")
    if value is None or ocr.get("stale"):
        return ordered
    ordered[-1] = (moment, {**signals, "manometer_ocr": round(float(value), 3)})
    return ordered


class DiagnosticsService:
    """Объединяет датчики и CV, хранит состояние правил с гистерезисом."""

    def __init__(self, catalog: RuleCatalog | None = None) -> None:
        self.catalog = catalog or load_catalog()
        settings = get_settings()
        self.activate_confidence = (
            settings.rule_activate_confidence
            if settings.rule_activate_confidence is not None
            else self.catalog.activate_confidence
        )
        self.release_confidence = (
            settings.rule_release_confidence
            if settings.rule_release_confidence is not None
            else self.catalog.release_confidence
        )
        if self.release_confidence >= self.activate_confidence:
            raise ValueError("release_confidence должен быть меньше activate_confidence")

    # Публичные методы

    def refresh(
        self,
        equipment_id: str,
        now: datetime | None = None,
        points: Sequence[tuple[datetime, dict[str, Any]]] | None = None,
        cv: Any | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Пересчитывает правила и (опционально) обновляет БД."""
        moment = _now(now)
        equipment = db.get_equipment(equipment_id)
        if equipment is None:
            raise KeyError(equipment_id)

        if points is None:
            points = db.get_recent_telemetry(equipment_id, seconds=90)
        if cv is None:
            cv = cv_state.snapshot(equipment_id, now=moment)
        scenario_row = db.get_scenario_state(equipment_id)
        started_at = _parse_dt((scenario_row or {}).get("started_at"))
        points = _with_ocr(points, cv, since=started_at)

        baseline = equipment.get("baseline") or {}
        evaluations = evaluate_all(
            self.catalog.rules,
            points,
            baseline,
            cv,
            now=moment,
        )
        # Подтверждение по окну важнее уверенности одного кадра: если окно
        # подтвердило дефект, создаём/усиливаем диагностику до dedupe.
        self._merge_analytics_visual(equipment_id, evaluations)
        # Схлопываем дубли, чтобы не держать два открытых события одного смысла.
        dedupe(
            evaluations,
            relevance_threshold=self.release_confidence,
            activate_confidence=self.activate_confidence,
        )
        # VLM-диагностика (visual_signs) и эвристики (visual_defect) не дублируются:
        # при совпадении узла остаётся более уверенное событие, слабое помечается.
        self._suppress_visual_defect(equipment_id, evaluations)
        # Стабильность по окну: одиночный кадр — info и просьба перепроверить.
        self._apply_analytics_stability(equipment_id, evaluations)

        if persist:
            self._resolve_missing_visual_nodes(equipment_id, evaluations, moment)
            self._apply(evaluations, equipment_id, moment)

        active_rows = db.get_open_diagnostics(equipment_id)
        by_rule = {evaluation.rule.id: evaluation for evaluation in evaluations}
        active = [
            self._enrich(row, by_rule.get(row["rule_id"])) for row in active_rows
        ]
        active.sort(
            key=lambda row: (
                -SEVERITY_RANK.get(row["severity"], 0),
                -float(row["confidence"]),
                row["rule_id"],
            )
        )

        active_ids = {row["rule_id"] for row in active}
        blocked = [
            {
                "rule_id": evaluation.rule.id,
                "title": evaluation.rule.title,
                "severity": evaluation.rule.severity,
                "blocked_by": evaluation.blocked_by,
                "reason": evaluation.blocked_reason,
            }
            for evaluation in sort_evaluations(evaluations)
            if evaluation.blocked_by and evaluation.rule.id not in active_ids
        ]
        # Правила, которые реально посчитались, но остались в норме. Без этого
        # списка в UI видно только сработавшие и заблокированные, и кажется,
        # что половина датчиков не анализируется.
        normal = [
            {
                "rule_id": evaluation.rule.id,
                "title": evaluation.rule.title,
                "severity": evaluation.rule.severity,
                "confidence": round(float(evaluation.confidence), 3),
                "activate_confidence": round(
                    float(
                        evaluation.rule.activate_confidence
                        if evaluation.rule.activate_confidence is not None
                        else self.activate_confidence
                    ),
                    3,
                ),
                "matched": bool(evaluation.matched),
                "conditions_matched": int(evaluation.conditions_matched),
                "conditions_total": int(evaluation.conditions_total),
                "suppressed_by": evaluation.suppressed_by,
            }
            for evaluation in sort_evaluations(evaluations)
            if not evaluation.blocked_by and evaluation.rule.id not in active_ids
        ]

        camera_age = cv_state.age_seconds(equipment_id, now=moment)
        history = [
            self._enrich(row, None)
            for row in db.get_resolved_diagnostics(equipment_id, limit=50)
        ]

        return {
            "equipment_id": equipment_id,
            "scenario": (scenario_row or {}).get("scenario", "normal"),
            "evaluated_at": db.iso(moment),
            "activate_confidence": self.activate_confidence,
            "release_confidence": self.release_confidence,
            "camera": {
                "available": cv.available,
                "source": cv.source,
                "age_sec": round(camera_age, 2) if camera_age is not None else None,
                "ttl_sec": get_settings().cv_ttl_sec,
                "observations": len(cv.observations),
                "ocr": getattr(cv, "ocr", None),
            },
            "active": active,
            "history": history,
            "blocked": blocked,
            "normal": normal,
        }

    def apply_evaluation(
        self,
        equipment_id: str,
        evaluation: RuleEvaluation,
        now: datetime | None = None,
    ) -> str:
        """Гистерезис для одного правила: activate / keep / resolve / none.

        Выделен отдельно, чтобы тестировать механику без симулятора.
        """
        moment = _now(now)
        rule = evaluation.rule
        row = db.find_open_diagnostic(equipment_id, rule.id)

        if evaluation.blocked_by:
            # Потеря камеры/OCR не снимает уже выданное предупреждение.
            return "keep" if row else "none"

        if row is None:
            threshold = (
                rule.activate_confidence
                if rule.activate_confidence is not None
                else self.activate_confidence
            )
            if evaluation.window_confirmed or evaluation.confidence >= threshold:
                db.create_diagnostic(
                    equipment_id=equipment_id,
                    rule_id=rule.id,
                    severity=evaluation.effective_severity,
                    confidence=evaluation.confidence,
                    evidence=evaluation.evidence,
                    message=evaluation.effective_message,
                    recommendation=evaluation.effective_recommendation,
                    overlay=evaluation.effective_overlay,
                    now=moment,
                )
                return "activate"
            return "none"

        if rule.latch_sec > 0:
            created = _parse_dt(row.get("created_at"))
            if created is not None and moment - created < timedelta(seconds=rule.latch_sec):
                self._update_row(row, evaluation, moment)
                return "keep"

        if not evaluation.window_confirmed and evaluation.confidence < self.release_confidence:
            db.resolve_diagnostic(row["id"], now=moment)
            return "resolve"

        self._update_row(row, evaluation, moment)
        return "keep"

    def ack(self, diagnostic_id: int) -> dict[str, Any] | None:
        """Подтверждение события с человекочитаемым названием правила."""
        row = db.ack_diagnostic(diagnostic_id)
        if row is None:
            return None
        return self._enrich(row, None)

    def rules_payload(self) -> dict[str, Any]:
        """Каталог для `GET /rules` + фактические пороги."""
        payload = catalog_as_dict(self.catalog)
        payload["activate_confidence"] = self.activate_confidence
        payload["release_confidence"] = self.release_confidence
        return payload

    # Внутреннее

    def _apply(
        self,
        evaluations: Sequence[RuleEvaluation],
        equipment_id: str,
        now: datetime,
    ) -> None:
        for evaluation in evaluations:
            if evaluation.suppressed_by:
                row = db.find_open_diagnostic(equipment_id, evaluation.rule.id)
                if row is not None:
                    # Пометка сохраняется в истории: видно, кто кого подавил.
                    db.update_diagnostic(
                        int(row["id"]),
                        suppressed_by=evaluation.suppressed_by,
                        now=now,
                    )
                    db.resolve_diagnostic(row["id"], now=now)
                continue
            self.apply_evaluation(equipment_id, evaluation, now=now)

    def _merge_analytics_visual(
        self,
        equipment_id: str,
        evaluations: list[RuleEvaluation],
    ) -> None:
        """Добавляет/усиливает `visual_defect` по подтверждённому окну.

        Активация правила не должна зависеть от confidence одного кадра:
        `presence_ratio >= 0.3` и `frames_present >= 3` уже посчитаны в
        `analytics`, поэтому подтверждённый узел гарантированно получает
        диагностику. Confidence для отображения — `mean_confidence` окна;
        факт активации помечается `window_confirmed=True`.
        """
        summary = analytics.summary(equipment_id)
        if not summary.get("frames"):
            return

        confirmed_nodes: dict[str, dict[str, Any]] = {}
        for node in summary.get("components", []):
            confirmed = [item for item in node["defects"] if item["confirmed"]]
            confirmed_signs = [
                item
                for item in node.get("signs") or []
                if item["status"] == "detected"
                and item["sign"] in VISUAL_DEFECT_CLASSES
            ]
            if confirmed or confirmed_signs:
                confirmed_nodes[node["component_id"]] = node
        if not confirmed_nodes:
            return

        # Если окно подтвердило дефект, незаматченная/заблокированная базовая
        # оценка больше не нужна: иначе она конфликтует по id с синтетической.
        evaluations[:] = [
            evaluation
            for evaluation in evaluations
            if not (
                evaluation.rule.dynamic == "visual_defect"
                and not evaluation.matched
            )
        ]

        visual = [
            evaluation
            for evaluation in evaluations
            if evaluation.rule.dynamic == "visual_defect" and evaluation.matched
        ]
        by_node: dict[str, RuleEvaluation] = {}
        for evaluation in visual:
            component_id = (evaluation.effective_overlay or {}).get("component_id")
            if component_id:
                by_node[str(component_id)] = evaluation

        rule = self.catalog.get("visual_defect")
        if rule is None:
            return
        for component_id, node in confirmed_nodes.items():
            evaluation = by_node.get(component_id)
            if evaluation is None:
                evaluation = self._window_visual_evaluation(rule, node)
                evaluations.append(evaluation)
                by_node[component_id] = evaluation
            self._mark_window_confirmed(evaluation, node)

        # Один базовый id на самый уверенный узел, остальным — суффикс.
        ordered = sorted(
            by_node.items(), key=lambda pair: (-pair[1].confidence, pair[0])
        )
        for index, (component_id, evaluation) in enumerate(ordered):
            rule_id = (
                "visual_defect"
                if index == 0
                else f"visual_defect_{component_id}"
            )
            evaluation.rule = evaluation.rule.model_copy(
                update={
                    "id": rule_id,
                    "dedupe_group": (
                        evaluation.rule.dedupe_group if index == 0 else None
                    ),
                }
            )

    def _window_visual_evaluation(
        self,
        rule: Rule,
        node: dict[str, Any],
    ) -> RuleEvaluation:
        """Синтетическая оценка `visual_defect` из подтверждённого окна."""
        component_id = str(node["component_id"])
        confirmed = [item for item in node["defects"] if item["confirmed"]]
        confirmed_signs = [
            item
            for item in node.get("signs") or []
            if item["status"] == "detected"
            and item["sign"] in VISUAL_DEFECT_CLASSES
        ]
        defect_classes = {item["class_name"] for item in confirmed}
        sign_classes = {
            item["sign"] for item in confirmed_signs
        } - defect_classes
        classes = sorted(defect_classes | sign_classes)
        component_ru = node.get("label") or analytics.COMPONENT_LABELS_RU.get(
            component_id, component_id
        )
        defect_confidence = (
            max(float(item["mean_confidence"]) for item in confirmed)
            if confirmed
            else 0.0
        )
        sign_confidence = (
            max(float(item["confidence"]) for item in confirmed_signs)
            if confirmed_signs
            else 0.0
        )
        confidence = round(max(defect_confidence, sign_confidence), 4)
        severity = "critical" if len(classes) >= 2 else "warning"
        defects_text = ", ".join(
            analytics.DEFECT_LABELS_RU.get(name, name) for name in classes
        )
        if confirmed:
            worst = max(confirmed, key=lambda item: item["mean_confidence"])
            frames_present = worst["frames_present"]
            frames_seen = worst["frames_seen_component"]
        else:
            worst_sign = max(
                confirmed_signs, key=lambda item: item["confidence"]
            )
            frames_present = worst_sign["frames_present"]
            frames_seen = worst_sign.get(
                "frames_seen_component", frames_present
            )
        message = (
            f"Визуальные признаки дефекта: {defects_text} на узле {component_ru}. "
            f"Подтверждено по {frames_present} из {frames_seen} кадров."
        )
        recommendations: list[str] = []
        for name in classes:
            text = rule.recommendations.get(name)
            if text and text not in recommendations:
                recommendations.append(text)
        overlay = {
            "component_id": component_id,
            "marker": rule.overlay.marker if rule.overlay else "highlight",
            "color": "red" if severity == "critical" else "orange",
            "label": f"Визуальный дефект: {defects_text}",
        }
        evidence = self._window_evidence(
            component_id, component_ru, confirmed, node
        )
        evidence.extend(
            self._window_sign_evidence(
                component_id,
                component_ru,
                [item for item in confirmed_signs if item["sign"] not in defect_classes],
            )
        )
        return RuleEvaluation(
            rule=rule,
            confidence=confidence,
            matched=True,
            evidence=evidence,
            conditions_matched=1,
            conditions_total=1,
            matched_weight=1.0,
            total_weight=1.0,
            severity=severity,
            message=message,
            recommendation=" ".join(recommendations) or rule.recommendation,
            overlay=overlay,
            window_confirmed=True,
        )

    def _window_sign_evidence(
        self,
        component_id: str,
        component_ru: str,
        signs: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for item in signs:
            evidence.append(
                {
                    "condition": (
                        f"CV: {item['sign']} подтверждён окном "
                        f"({item['frames_present']} из "
                        f"{item.get('frames_seen_component', item['frames_present'])} "
                        "кадров)"
                    ),
                    "signal": item["sign"],
                    "op": "cv",
                    "actual": item["confidence"],
                    "baseline": None,
                    "threshold": item.get("threshold"),
                    "matched": True,
                    "weight": 1.0,
                    "score": item.get("score", item["confidence"]),
                    "area": item.get("area"),
                    "area_pct": item.get("area_pct"),
                    "class": item["sign"],
                    "sign": item["sign"],
                    "sign_label": item["label"],
                    "status": item["status"],
                    "presence_ratio": item["presence_ratio"],
                    "sources": item["sources"],
                    "component": component_id,
                    "component_ru": component_ru,
                    "analytics": {
                        "frames_present": item["frames_present"],
                        "frames_seen_component": item.get(
                            "frames_seen_component", item["frames_present"]
                        ),
                        "presence_ratio": item["presence_ratio"],
                        "mean_confidence": item["confidence"],
                        "confirmed": True,
                    },
                }
            )
        return evidence

    def _mark_window_confirmed(
        self,
        evaluation: RuleEvaluation,
        node: dict[str, Any],
    ) -> None:
        """Переводит существующую оценку в состояние «подтверждено окном»."""
        rule = self.catalog.get("visual_defect")
        if rule is None:
            return
        template = self._window_visual_evaluation(rule, node)
        evaluation.confidence = template.confidence
        evaluation.severity = template.severity
        evaluation.message = template.message
        evaluation.recommendation = template.recommendation
        evaluation.overlay = template.overlay
        evaluation.evidence = template.evidence
        evaluation.window_confirmed = True

    def _window_evidence(
        self,
        component_id: str,
        component_ru: str,
        confirmed: Sequence[dict[str, Any]],
        node: dict[str, Any],
    ) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        sign_items = {
            str(item["sign"]): item for item in node.get("signs") or []
        }
        for item in confirmed:
            class_name = str(item["class_name"])
            sign_id = heuristic_sign_for_class(class_name) or class_name
            definition = sign_definition(sign_id)
            sign_stats = sign_items.get(sign_id)
            evidence.append(
                {
                    "condition": (
                        f"CV: {class_name} подтверждён окном "
                        f"({item['frames_present']} из "
                        f"{item['frames_seen_component']} кадров)"
                    ),
                    "signal": class_name,
                    "op": "cv",
                    "actual": item["mean_confidence"],
                    "baseline": None,
                    "threshold": None,
                    "matched": True,
                    "weight": 1.0,
                    "score": item["mean_confidence"],
                    "area": sign_stats.get("area") if sign_stats else None,
                    "area_pct": (
                        sign_stats.get("area_pct") if sign_stats else None
                    ),
                    "threshold": (
                        sign_stats.get("threshold") if sign_stats else None
                    ),
                    "class": class_name,
                    "class_ru": analytics.DEFECT_LABELS_RU.get(
                        class_name, class_name
                    ),
                    "sign": sign_id,
                    "sign_label": definition.label if definition else class_name,
                    "status": (
                        sign_stats["status"] if sign_stats else "detected"
                    ),
                    "presence_ratio": (
                        sign_stats["presence_ratio"]
                        if sign_stats
                        else item["presence_ratio"]
                    ),
                    "sources": (
                        sign_stats["sources"] if sign_stats else ["heuristics"]
                    ),
                    "component": component_id,
                    "component_ru": component_ru,
                    "analytics": {
                        "frames_present": item["frames_present"],
                        "frames_seen_component": item["frames_seen_component"],
                        "presence_ratio": item["presence_ratio"],
                        "mean_confidence": item["mean_confidence"],
                        "confirmed": True,
                    },
                }
            )
        return evidence

    def _suppress_visual_defect(
        self,
        equipment_id: str,
        evaluations: list[RuleEvaluation],
    ) -> None:
        """Не даёт `visual_defect` воскреснуть, пока открыта более уверенная VLM."""
        row = db.find_open_diagnostic(equipment_id, VISUAL_SIGNS_RULE_ID)
        if row is None:
            return
        vision_confidence = float(row.get("confidence") or 0.0)
        vision_evidence = row.get("evidence") or []
        vision_component = next(
            (
                item.get("component")
                for item in vision_evidence
                if isinstance(item, dict) and item.get("component")
            ),
            None,
        )
        for evaluation in evaluations:
            if (
                evaluation.rule.dynamic != "visual_defect"
                or not evaluation.matched
            ):
                continue
            overlay = evaluation.effective_overlay or {}
            node = overlay.get("component_id")
            same_node = (
                node is None
                or vision_component is None
                or node == vision_component
            )
            if same_node and vision_confidence >= evaluation.confidence:
                evaluation.suppressed_by = VISUAL_SIGNS_RULE_ID

    def _apply_analytics_stability(
        self,
        equipment_id: str,
        evaluations: list[RuleEvaluation],
    ) -> None:
        """Понижает `visual_defect` до info, если окно не подтвердило дефект.

        Дефект считается подтверждённым, если он присутствовал в
        `ANALYTICS_CONFIRM_FRAMES` кадрах и `presence_ratio` не ниже
        `ANALYTICS_CONFIRM_RATIO`. Иначе правило остаётся активным (чтобы
        инженер увидел сигнал), но severity = info, цвет серый, а в тексте
        появляется «единичное наблюдение, требуется повторная проверка».
        В evidence каждого детектора добавляется блок `analytics`.
        """
        summary = analytics.summary(equipment_id)
        if not summary.get("frames"):
            return
        by_component = {
            item["component_id"]: item for item in summary["components"]
        }
        suffix = "Единичное наблюдение, требуется повторная проверка."
        for evaluation in evaluations:
            if (
                evaluation.rule.dynamic != "visual_defect"
                or not evaluation.matched
            ):
                continue
            overlay = evaluation.effective_overlay or {}
            component_id = overlay.get("component_id")
            node = by_component.get(component_id)
            if node is None:
                continue
            confirmed_flags: list[bool] = []
            for entry in evaluation.evidence:
                class_name = entry.get("class")
                stats = next(
                    (
                        item
                        for item in node["defects"]
                        if item["class_name"] == class_name
                    ),
                    None,
                )
                if stats is None:
                    continue
                entry["analytics"] = {
                    "frames_present": stats["frames_present"],
                    "frames_seen_component": stats["frames_seen_component"],
                    "presence_ratio": stats["presence_ratio"],
                    "mean_confidence": stats["mean_confidence"],
                    "confirmed": stats["confirmed"],
                }
                sign_id = heuristic_sign_for_class(str(class_name)) or str(class_name)
                sign_stats = next(
                    (
                        item
                        for item in node.get("signs") or []
                        if item["sign"] == sign_id
                    ),
                    None,
                )
                if sign_stats is not None:
                    entry["sign"] = sign_id
                    entry["status"] = sign_stats["status"]
                    entry["presence_ratio"] = sign_stats["presence_ratio"]
                    entry["sources"] = sign_stats["sources"]
                    entry["score"] = sign_stats.get("score")
                    entry["area"] = sign_stats.get("area")
                    entry["area_pct"] = sign_stats.get("area_pct")
                    entry["threshold"] = sign_stats.get("threshold")
                confirmed_flags.append(bool(stats["confirmed"]))
            if confirmed_flags and not any(confirmed_flags):
                evaluation.severity = "info"
                evaluation.overlay = {**overlay, "color": "grey"}
                base = evaluation.effective_message.rstrip()
                evaluation.message = f"{base} {suffix}"

    def _resolve_missing_visual_nodes(
        self,
        equipment_id: str,
        evaluations: Sequence[RuleEvaluation],
        now: datetime,
    ) -> None:
        """Закрывает поузловые `visual_defect_<узел>`, которых больше нет.

        Базовое правило `visual_defect` при отсутствии дефектов приходит с
        `matched=False` и закрывается обычным гистерезисом; синтетические id
        для дополнительных узлов в списке оценок отсутствуют, поэтому их
        нужно закрыть явно.
        """
        current = {
            evaluation.rule.id
            for evaluation in evaluations
            if evaluation.rule.dynamic == "visual_defect"
        }
        for row in db.get_open_diagnostics(equipment_id):
            rule_id = str(row.get("rule_id") or "")
            if rule_id.startswith("visual_defect_") and rule_id not in current:
                db.resolve_diagnostic(int(row["id"]), now=now)

    def _update_row(
        self,
        row: dict[str, Any],
        evaluation: RuleEvaluation,
        now: datetime,
    ) -> None:
        db.update_diagnostic(
            int(row["id"]),
            severity=evaluation.effective_severity,
            confidence=evaluation.confidence,
            evidence=evaluation.evidence,
            message=evaluation.effective_message,
            recommendation=evaluation.effective_recommendation,
            overlay=evaluation.effective_overlay,
            now=now,
        )

    def _enrich(
        self,
        row: dict[str, Any],
        evaluation: RuleEvaluation | None,
    ) -> dict[str, Any]:
        rule = self.catalog.get(row["rule_id"])
        enriched = dict(row)
        if rule is not None:
            enriched["title"] = rule.title
        elif row["rule_id"] == VISUAL_SIGNS_RULE_ID:
            enriched["title"] = VISUAL_SIGNS_TITLE
        elif str(row["rule_id"]).startswith("visual_defect"):
            base = self.catalog.get("visual_defect")
            enriched["title"] = base.title if base else "Визуальные признаки дефекта"
        else:
            enriched["title"] = row["rule_id"]
        enriched["blocked_by"] = evaluation.blocked_by if evaluation else None
        enriched["blocked_reason"] = evaluation.blocked_reason if evaluation else None
        enriched.setdefault("suppressed_by", None)
        if not enriched.get("overlay") and rule and rule.overlay:
            enriched["overlay"] = rule.overlay.model_dump()
        # Явная привязка к узлу: у visual_defect/visual_signs событие всегда
        # про конкретный узел, клиенту не нужно разбирать overlay.
        overlay = enriched.get("overlay") or {}
        component_id = overlay.get("component_id")
        if component_id is None:
            component_id = next(
                (
                    item.get("component")
                    for item in (enriched.get("evidence") or [])
                    if isinstance(item, dict) and item.get("component")
                ),
                None,
            )
        enriched["component_id"] = component_id
        return enriched


__all__ = ["DiagnosticsService"]
