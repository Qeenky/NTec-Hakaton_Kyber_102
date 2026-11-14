import 'package:material_ui/material_ui.dart';

import 'app_state.dart';
import 'screens/home_shell.dart';
import 'theme/app_theme.dart';

class InspectorApp extends StatelessWidget {
  const InspectorApp({super.key});

  @override
  Widget build(BuildContext context) {
    final app = AppScope.maybeOf(context);
    return MaterialApp(
      title: 'NTec Инспектор',
      debugShowCheckedModeBanner: false,
      theme: buildLightTheme(),
      darkTheme: buildDarkTheme(),
      themeMode: app?.themeMode ?? ThemeMode.system,
      home: const HomeShell(),
    );
  }
}
