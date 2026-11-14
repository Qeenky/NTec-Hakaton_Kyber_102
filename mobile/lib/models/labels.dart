/// Русские подписи для кодов из контракта.
/// Никаких сетевых ресурсов: всё зашито в APK.
library;

const Map<String, String> componentLabels = {
  'motor': 'Электродвигатель',
  'pump': 'Насос',
  'coupling_guard': 'Кожух муфты',
  'flange': 'Фланцевое соединение',
  'manometer': 'Манометр',
  'oil_leak': 'Утечка масла',
  'corrosion': 'Коррозия',
  'crack': 'Трещина',
  'guard_damage': 'Повреждение кожуха',
};

String componentLabel(String? code) {
  if (code == null || code.isEmpty) return 'Неизвестный узел';
  return componentLabels[code] ?? code;
}

String componentStatusLabel(String? status) {
  switch (status) {
    case 'ok':
      return 'Норма';
    case 'check_required':
      return 'Требует внимания';
    case 'possible_defect':
      return 'Возможный дефект';
    case 'insufficient_data':
      return 'Нет данных';
    case 'exhausted':
      return 'Ресурс исчерпан';
    default:
      return status == null || status.isEmpty ? 'Нет данных' : status;
  }
}

String visionVerdictLabel(String? verdict) {
  switch (verdict) {
    case 'defect':
      return 'Обнаружены признаки дефекта';
    case 'no_defect':
      return 'Видимых дефектов не обнаружено';
    case 'insufficient':
      return 'Недостаточно данных';
    default:
      return verdict == null || verdict.isEmpty ? 'Недостаточно данных' : verdict;
  }
}

String visionSignLabel(String key) {
  switch (key) {
    case 'corrosion':
      return 'Коррозия';
    case 'paint_peeling':
      return 'Отслоение краски';
    case 'oil_leak':
      return 'Подтёк масла';
    case 'mechanical_damage':
      return 'Механическое повреждение';
    default:
      return key;
  }
}

String visionSignValueLabel(bool? value) {
  if (value == null) return 'не видно';
  return value ? 'есть' : 'нет';
}

String severityLabel(String? severity) {
  switch (severity) {
    case 'critical':
      return 'Критично';
    case 'warning':
      return 'Предупреждение';
    case 'info':
      return 'Информация';
    default:
      return severity ?? '';
  }
}

String blockedReasonLabel(String? code, String? reason) {
  if (reason != null && reason.isNotEmpty) return reason;
  switch (code) {
    case 'camera_unavailable':
      return 'нет свежих кадров с камеры';
    case 'ocr_unavailable':
      return 'нет показания манометра';
    case 'insufficient_window':
      return 'недостаточно данных за окно наблюдения';
    default:
      if (code != null && code.startsWith('signal_missing:')) {
        final signal = code.substring('signal_missing:'.length);
        return 'нет сигнала «${signalLabel(signal)}»';
      }
      return code ?? 'причина неизвестна';
  }
}

String scenarioLabel(String? code) {
  switch (code) {
    case 'normal':
      return 'норма';
    case 'bearing_wear':
      return 'износ подшипника';
    case 'cavitation':
      return 'кавитация';
    case 'misalignment':
      return 'несоосность';
    case 'impeller_wear':
      return 'износ рабочего колеса';
    case 'seal_leak':
      return 'течь уплотнения';
    case 'looseness':
      return 'ослабление крепежа';
    case 'lubrication_degradation':
      return 'деградация смазки';
    case 'electrical_contact':
      return 'проблема контакта';
    case 'gauge_drift':
      return 'дрейф манометра';
    case 'guard_removed':
      return 'снят кожух';
    default:
      return code ?? '—';
  }
}

String ocrErrorLabel(String? code) {
  switch (code) {
    case null:
    case '':
      return 'нет показания';
    case 'no_needle':
      return 'стрелка не найдена';
    case 'no_gauge':
    case 'gauge_not_found':
      return 'манометр не найден';
    case 'low_confidence':
      return 'низкая достоверность';
    default:
      return code;
  }
}

String detectorLabel(String? code) {
  switch (code) {
    case 'board':
      return 'AR-планшет';
    case 'fake':
      return 'заглушка';
    case 'yolo':
      return 'YOLO';
    default:
      return code ?? '—';
  }
}

String boardReasonLabel(String? reason) {
  switch (reason) {
    case 'no_markers':
      return 'Маркеры не найдены. Наведите камеру на планшет целиком.';
    case 'partial':
      return 'Видно меньше трёх маркеров. Отойдите чуть дальше.';
    case 'low_quality':
      return 'Планшет найден неточно. Приблизьтесь и держите ровнее.';
    case 'detector_fake':
      return 'Включён детектор-заглушка: AR-зоны недоступны.';
    case 'invalid_image':
      return 'Кадр не распознан сервером.';
    case 'error':
      return 'Ошибка обработки кадра на сервере.';
    default:
      return reason == null || reason.isEmpty
          ? 'Наведите камеру на AR-планшет.'
          : reason;
  }
}

const Map<String, String> signalLabels = {
  'vibration_rms': 'Вибрация',
  'vibration_axial': 'Осевая вибрация',
  'vibration_hf': 'ВЧ-вибрация',
  'vibration_lf_1x': 'НЧ 1×',
  'temperature_bearing': 'Темп. подшипника',
  'temperature_motor': 'Темп. двигателя',
  'current_a': 'Ток фазы A',
  'current_b': 'Ток фазы B',
  'current_c': 'Ток фазы C',
  'pressure_inlet': 'Давление вход',
  'pressure_outlet': 'Давление выход',
  'flow': 'Расход',
  'efficiency': 'КПД',
  'runtime_hours': 'Наработка',
  'oil_level': 'Уровень масла',
  'oil_color': 'Цвет масла',
  'noise_db': 'Шум',
  'manometer_ocr': 'Манометр (OCR)',
};

const Map<String, String> signalUnits = {
  'vibration_rms': 'мм/с',
  'vibration_axial': 'мм/с',
  'vibration_hf': 'мм/с',
  'vibration_lf_1x': 'мм/с',
  'temperature_bearing': '°C',
  'temperature_motor': '°C',
  'current_a': 'А',
  'current_b': 'А',
  'current_c': 'А',
  'pressure_inlet': 'бар',
  'pressure_outlet': 'бар',
  'flow': 'м³/ч',
  'efficiency': '%',
  'runtime_hours': 'м/ч',
  'oil_level': '%',
  'noise_db': 'дБ',
  'manometer_ocr': 'бар',
};

String signalLabel(String key) => signalLabels[key] ?? key;
String signalUnit(String key) => signalUnits[key] ?? '';

/// Значение сигнала с нужным числом знаков; строки (`oil_color`) — как есть.
String formatSignal(String key, Object? value) {
  if (value == null) return '—';
  if (value is num) {
    final digits = switch (key) {
      'runtime_hours' => 0,
      'efficiency' || 'oil_level' || 'flow' => 1,
      'pressure_inlet' || 'pressure_outlet' || 'manometer_ocr' => 2,
      _ => 1,
    };
    return value.toStringAsFixed(digits);
  }
  if (value is String) {
    switch (value) {
      case 'light':
        return 'светлое';
      case 'dark':
        return 'тёмное';
    }
    return value;
  }
  return value.toString();
}
