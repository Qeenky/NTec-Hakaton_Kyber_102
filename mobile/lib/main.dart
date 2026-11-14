import 'package:flutter/widgets.dart';

import 'app.dart';
import 'app_state.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final state = await AppState.create();
  state.start();
  runApp(AppScope(state: state, child: const InspectorApp()));
}
