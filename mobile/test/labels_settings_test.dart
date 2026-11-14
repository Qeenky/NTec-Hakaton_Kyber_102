import 'package:flutter_test/flutter_test.dart';
import 'package:ntec_inspector/models/labels.dart';
import 'package:ntec_inspector/services/server_settings.dart';

void main() {
  group('Русские подписи', () {
    test('узлы и статусы', () {
      expect(componentLabel('manometer'), 'Манометр');
      expect(componentLabel('coupling_guard'), 'Кожух муфты');
      expect(componentLabel(null), 'Неизвестный узел');
      expect(componentStatusLabel('ok'), 'Норма');
      expect(componentStatusLabel('check_required'), 'Требует внимания');
      expect(componentStatusLabel('possible_defect'), 'Возможный дефект');
      expect(severityLabel('critical'), 'Критично');
      expect(severityLabel('info'), 'Информация');
    });

    test('причины блокировки правил', () {
      expect(blockedReasonLabel('camera_unavailable', null),
          'нет свежих кадров с камеры');
      expect(blockedReasonLabel('ocr_unavailable', 'нет OCR'),
          'нет OCR');
      expect(blockedReasonLabel('signal_missing:vibration_hf', null),
          contains('ВЧ-вибрация'));
      expect(blockedReasonLabel('insufficient_window', null),
          contains('окно'));
    });

    test('подсказки планшета', () {
      expect(boardReasonLabel('no_markers'), contains('Маркеры не найдены'));
      expect(boardReasonLabel('partial'), contains('трёх маркеров'));
      expect(boardReasonLabel('detector_fake'), contains('заглушка'));
      expect(boardReasonLabel(null), contains('AR-планшет'));
    });

    test('форматирование сигналов телеметрии', () {
      expect(formatSignal('vibration_rms', 3.42), '3.4');
      expect(formatSignal('pressure_outlet', 8.383), '8.38');
      expect(formatSignal('efficiency', 77.6), '77.6');
      expect(formatSignal('oil_color', 'dark'), 'тёмное');
      expect(formatSignal('runtime_hours', 12450.5), '12451');
      expect(formatSignal('flow', null), '—');
      expect(signalUnit('temperature_bearing'), '°C');
      expect(signalLabel('vibration_rms'), 'Вибрация');
    });
  });

  group('ServerSettings', () {
    test('адрес по умолчанию', () {
      expect(ServerSettings.defaults.baseUrl, 'http://192.168.137.1:8000');
      expect(ServerSettings.defaults.wsBaseUrl, 'ws://192.168.137.1:8000');
    });

    test('разбирает разные формы записи', () {
      expect(ServerSettings.tryParse('192.168.137.1:8000')?.authority,
          '192.168.137.1:8000');
      expect(ServerSettings.tryParse(' http://10.0.0.5:9000/ ')?.authority,
          '10.0.0.5:9000');
      expect(ServerSettings.tryParse('127.0.0.1')?.port, 8000);
      expect(ServerSettings.tryParse('ws://localhost:8001')?.host, 'localhost');
      expect(ServerSettings.tryParse(''), isNull);
      expect(ServerSettings.tryParse('плохой адрес'), isNull);
      expect(ServerSettings.tryParse('host:99999'), isNull);
      expect(ServerSettings.parse('плохой').authority,
          ServerSettings.defaultAddress);
    });

    test('печатает и сравнивает', () {
      const settings = ServerSettings(host: '127.0.0.1', port: 8000);
      expect(settings.toString(), '127.0.0.1:8000');
      expect(settings, const ServerSettings(host: '127.0.0.1', port: 8000));
      expect(settings == settings.copyWith(port: 8001), isFalse);
    });
  });
}
