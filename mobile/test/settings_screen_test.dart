import 'package:flutter_test/flutter_test.dart';
import 'package:material_ui/material_ui.dart';
import 'package:ntec_inspector/app_state.dart';
import 'package:ntec_inspector/screens/settings_screen.dart';
import 'package:ntec_inspector/services/server_settings.dart';
import 'package:ntec_inspector/theme/app_theme.dart';
import 'package:shared_preferences/shared_preferences.dart';

Future<AppState> _buildState() async {
  SharedPreferences.setMockInitialValues(<String, Object>{});
  final prefs = await SharedPreferences.getInstance();
  final store = SettingsStore(prefs);
  return AppState(store: store, settings: store.load());
}

Widget _wrap(AppState state) {
  return AppScope(
    state: state,
    child: MaterialApp(
      theme: buildAppTheme(),
      home: const SettingsScreen(),
    ),
  );
}

void main() {
  testWidgets('настройки: подставляет адрес по умолчанию', (tester) async {
    final state = await _buildState();
    addTearDown(state.dispose);

    await tester.pumpWidget(_wrap(state));
    await tester.pump();

    final field = tester.widget<TextField>(
      find.byKey(const Key('server-address-field')),
    );
    expect(field.controller?.text, ServerSettings.defaultAddress);
    expect(find.text('Сохранить'), findsOneWidget);
    expect(find.text('Проверить связь'), findsOneWidget);
    expect(find.text('Подключение'), findsOneWidget);
  });

  testWidgets('настройки: показывает ошибку для некорректного адреса',
      (tester) async {
    final state = await _buildState();
    addTearDown(state.dispose);

    await tester.pumpWidget(_wrap(state));
    await tester.pump();

    await tester.enterText(
      find.byKey(const Key('server-address-field')),
      'плохой адрес',
    );
    await tester.tap(find.byKey(const Key('save-address-button')));
    await tester.pump();

    expect(find.textContaining('Введите адрес'), findsOneWidget);
    expect(state.settings, ServerSettings.defaults);
  });

  testWidgets('настройки: кнопка USB подставляет 127.0.0.1:8000',
      (tester) async {
    tester.view.physicalSize = const Size(1080, 3600);
    tester.view.devicePixelRatio = 3;
    addTearDown(() {
      tester.view.resetPhysicalSize();
      tester.view.resetDevicePixelRatio();
    });

    final state = await _buildState();
    addTearDown(state.dispose);

    await tester.pumpWidget(_wrap(state));
    await tester.pump();

    final usbButton = find.textContaining('Подставить адрес для USB');
    await tester.ensureVisible(usbButton);
    await tester.pumpAndSettle();
    await tester.tap(usbButton);
    await tester.pump();

    final field = tester.widget<TextField>(
      find.byKey(const Key('server-address-field')),
    );
    expect(field.controller?.text, ServerSettings.usbAddress);
  });
}
