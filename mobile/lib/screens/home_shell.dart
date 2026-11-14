import 'package:material_ui/material_ui.dart';

import '../theme/app_theme.dart';
import 'analysis_tab_screen.dart';
import 'defects_tab_screen.dart';
import 'equipment_tab_screen.dart';
import 'scanner_screen.dart';

/// Корневой экран с 4 нижними вкладками:
/// «Осмотр», «Дефекты», «Анализ», «Оборудование».
class HomeShell extends StatefulWidget {
  const HomeShell({super.key, this.initialIndex = 0});

  final int initialIndex;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  late int _currentIndex = widget.initialIndex;

  void _selectTab(int index) {
    if (_currentIndex == index) return;
    setState(() => _currentIndex = index);
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return Scaffold(
      body: IndexedStack(
        index: _currentIndex,
        children: [
          ScannerScreen(isActive: _currentIndex == 0),
          DefectsTabScreen(onStartInspectionTap: () => _selectTab(0)),
          const AnalysisTabScreen(),
          const EquipmentTabScreen(),
        ],
      ),
      bottomNavigationBar: Container(
        decoration: BoxDecoration(
          border: Border(top: BorderSide(color: palette.outline)),
        ),
        child: NavigationBar(
          key: const Key('main-bottom-nav'),
          selectedIndex: _currentIndex,
          onDestinationSelected: _selectTab,
          destinations: const [
            NavigationDestination(
              key: Key('tab-inspect'),
              icon: Icon(Icons.qr_code_scanner_outlined),
              selectedIcon: Icon(Icons.qr_code_scanner),
              label: 'Осмотр',
            ),
            NavigationDestination(
              key: Key('tab-defects'),
              icon: Icon(Icons.warning_amber_outlined),
              selectedIcon: Icon(Icons.warning_amber_rounded),
              label: 'Дефекты',
            ),
            NavigationDestination(
              key: Key('tab-analysis'),
              icon: Icon(Icons.insights_outlined),
              selectedIcon: Icon(Icons.insights),
              label: 'Анализ',
            ),
            NavigationDestination(
              key: Key('tab-equipment'),
              icon: Icon(Icons.precision_manufacturing_outlined),
              selectedIcon: Icon(Icons.precision_manufacturing),
              label: 'Оборудование',
            ),
          ],
        ),
      ),
    );
  }
}
