import 'package:material_ui/material_ui.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../app_state.dart';
import '../models/component_info.dart';
import '../models/equipment.dart';
import '../services/api_service.dart';
import '../theme/app_theme.dart';
import '../widgets/connection_indicator.dart';
import 'ar_inspection_screen.dart';
import 'equipment_screen.dart';
import 'settings_screen.dart';

/// Стартовый экран (вкладка «Осмотр»): QR-сканер, быстрый старт AR в 1 тап и выбор из списка.
class ScannerScreen extends StatefulWidget {
  const ScannerScreen({super.key, this.isActive = true});

  final bool isActive;

  @override
  State<ScannerScreen> createState() => _ScannerScreenState();
}

class _ScannerScreenState extends State<ScannerScreen> {
  static const List<String> _quickIds = <String>['PUMP-001', 'PUMP-002'];

  late final MobileScannerController _scanner;
  bool _busy = false;
  String? _lastError;

  @override
  void initState() {
    super.initState();
    _scanner = MobileScannerController(
      formats: const [BarcodeFormat.qrCode],
      detectionSpeed: DetectionSpeed.noDuplicates,
      facing: CameraFacing.back,
      torchEnabled: false,
    );
  }

  @override
  void didUpdateWidget(covariant ScannerScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.isActive != widget.isActive) {
      if (widget.isActive && !_busy) {
        try {
          _scanner.start();
        } catch (_) {}
      } else {
        try {
          _scanner.stop();
        } catch (_) {}
      }
    }
  }

  @override
  void dispose() {
    _scanner.dispose();
    super.dispose();
  }

  String? _extractCode(BarcodeCapture capture) {
    for (final barcode in capture.barcodes) {
      final raw = barcode.rawValue?.trim();
      if (raw == null || raw.isEmpty) continue;
      var value = raw.split(RegExp(r'[\r\n]')).first.trim();
      if (value.contains('?')) value = value.split('?').first;
      if (value.contains('/')) {
        final parts = value.split('/').where((part) => part.isNotEmpty).toList();
        if (parts.isNotEmpty) value = parts.last;
      }
      if (value.isNotEmpty) return value.toUpperCase();
    }
    return null;
  }

  Future<void> _onDetect(BarcodeCapture capture) async {
    if (!widget.isActive) return;
    final code = _extractCode(capture);
    if (code == null || _busy) return;
    await _openEquipment(code);
  }

  Equipment _fallbackEquipment(String code) {
    return Equipment(
      id: code,
      name: code == 'PUMP-002'
          ? 'Насосный агрегат (дефектный участок)'
          : 'Насосный агрегат',
      components: const <ComponentInfo>[
        ComponentInfo(id: 1, name: 'motor', kind: 'component', condition: 'ok'),
        ComponentInfo(id: 2, name: 'pump', kind: 'component', condition: 'ok'),
        ComponentInfo(id: 3, name: 'coupling_guard', kind: 'component', condition: 'ok'),
        ComponentInfo(id: 4, name: 'flange', kind: 'component', condition: 'ok'),
        ComponentInfo(id: 5, name: 'manometer', kind: 'component', condition: 'ok'),
      ],
    );
  }

  /// Демо-путь в один тап: сразу открывает AR-осмотр выбранного агрегата.
  Future<void> _startOneTapAr(String code) async {
    if (_busy || !mounted) return;
    setState(() {
      _busy = true;
      _lastError = null;
    });
    final app = AppScope.of(context);
    await app.selectEquipment(code);
    Equipment equipment;
    try {
      equipment = await app.api.fetchEquipment(code);
    } catch (_) {
      equipment = _fallbackEquipment(code);
    }
    if (!mounted) return;
    try {
      await _scanner.stop();
    } catch (_) {}
    if (!mounted) return;
    setState(() => _busy = false);
    await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => ArInspectionScreen(equipment: equipment),
      ),
    );
    if (!mounted) return;
    if (widget.isActive) {
      try {
        await _scanner.start();
      } catch (_) {}
    }
  }

  Future<void> _openEquipment(String code) async {
    if (_busy || !mounted) return;
    setState(() {
      _busy = true;
      _lastError = null;
    });
    final app = AppScope.of(context);
    await app.selectEquipment(code);
    try {
      await app.api.fetchEquipment(code);
      if (!mounted) return;
      try {
        await _scanner.stop();
      } catch (_) {}
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => EquipmentScreen(equipmentId: code),
        ),
      );
      if (!mounted) return;
      if (widget.isActive) {
        try {
          await _scanner.start();
        } catch (_) {}
      }
    } on ApiException catch (exception) {
      if (!mounted) return;
      setState(() => _lastError = '${exception.message} · код «$code»');
      await _showNotFound(code, exception.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _showNotFound(String code, String message) async {
    final action = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Агрегат не найден'),
        content: Text(
          '$message\n\nКод: $code\n\nМожно выбрать оборудование вручную из списка.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop('retry'),
            child: const Text('Повторить'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop('manual'),
            child: const Text('Список'),
          ),
        ],
      ),
    );
    if (!mounted) return;
    if (action == 'manual') {
      await _pickManually();
    } else if (action == 'retry') {
      setState(() => _busy = false);
    }
  }

  Future<void> _pickManually() async {
    final app = AppScope.of(context);
    final palette = AppPalette.of(context);
    final selected = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      backgroundColor: palette.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (sheetContext) => _EquipmentPicker(api: app.api),
    );
    if (selected != null && mounted) {
      await _openEquipment(selected);
    }
  }

  void _openSettings() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => const SettingsScreen()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final app = AppScope.of(context);
    final selectedId = app.selectedEquipmentId;

    return Scaffold(
      appBar: AppBar(
        title: const Text('NTec · Инспектор'),
        actions: [
          const Padding(
            padding: EdgeInsets.only(right: 4),
            child: Center(child: ConnectionIndicator()),
          ),
          const ThemeModeButton(),
          IconButton(
            tooltip: 'Настройки подключения',
            onPressed: _openSettings,
            icon: const Icon(Icons.settings_outlined),
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(16),
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    MobileScanner(
                      controller: _scanner,
                      onDetect: _onDetect,
                      onDetectError: (_, _) {},
                      errorBuilder: (context, error) =>
                          const _CameraErrorView(),
                      placeholderBuilder: (context) => ColoredBox(
                        color: palette.background,
                        child: const Center(child: CircularProgressIndicator()),
                      ),
                    ),
                    const IgnorePointer(
                      child: CustomPaint(painter: _ScannerFramePainter()),
                    ),
                    Positioned(
                      left: 14,
                      right: 14,
                      top: 14,
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 12,
                          vertical: 8,
                        ),
                        decoration: BoxDecoration(
                          color: const Color(0xFF0B0F14).withValues(alpha: 0.72),
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: const Text(
                          'Наведите камеру на QR-код или начните осмотр в 1 тап',
                          textAlign: TextAlign.center,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 13,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ),
                    if (_busy)
                      ColoredBox(
                        color: Colors.black.withValues(alpha: 0.45),
                        child: const Center(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              CircularProgressIndicator(),
                              SizedBox(height: 12),
                              Text(
                                'Загружаем карточку…',
                                style: TextStyle(
                                  color: Colors.white,
                                  fontSize: 14,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 14),
            decoration: BoxDecoration(
              color: palette.surface,
              border: Border(top: BorderSide(color: palette.outline)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (_lastError != null) ...[
                  Text(
                    _lastError!,
                    style: TextStyle(
                      color: palette.red,
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 8),
                ],
                Row(
                  children: [
                    Text(
                      'Агрегат:',
                      style: TextStyle(
                        color: palette.textMuted,
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(width: 8),
                    for (final code in _quickIds) ...[
                      ChoiceChip(
                        key: Key('quick-eq-$code'),
                        label: Text(code),
                        selected: selectedId == code,
                        onSelected: (_) => app.selectEquipment(code),
                      ),
                      const SizedBox(width: 8),
                    ],
                    const Spacer(),
                    TextButton(
                      key: const Key('quick-open-card-button'),
                      onPressed: _busy ? null : () => _openEquipment(selectedId),
                      child: const Text('Паспорт ›'),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                FilledButton.icon(
                  key: const Key('quick-ar-button'),
                  onPressed: _busy ? null : () => _startOneTapAr(selectedId),
                  icon: const Icon(Icons.view_in_ar_outlined),
                  label: Text('Начать AR-осмотр · $selectedId'),
                ),
                const SizedBox(height: 8),
                OutlinedButton.icon(
                  key: const Key('manual-pick-button'),
                  onPressed: _busy ? null : _pickManually,
                  icon: const Icon(Icons.list_alt),
                  label: const Text('Выбрать оборудование вручную'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _CameraErrorView extends StatelessWidget {
  const _CameraErrorView();

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return ColoredBox(
      color: palette.background,
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.no_photography_outlined,
                  size: 48, color: palette.textMuted),
              const SizedBox(height: 12),
              Text(
                'Нет доступа к камере',
                style: TextStyle(
                  color: palette.text,
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 6),
              Text(
                'Нажмите «Начать AR-осмотр» ниже или выберите оборудование из списка.',
                textAlign: TextAlign.center,
                maxLines: 2,
                style: TextStyle(color: palette.textSecondary, fontSize: 14),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ScannerFramePainter extends CustomPainter {
  const _ScannerFramePainter();

  @override
  void paint(Canvas canvas, Size size) {
    final side = size.shortestSide * 0.62;
    final rect = Rect.fromCenter(
      center: size.center(Offset.zero),
      width: side,
      height: side,
    );
    final mask = Path.combine(
      PathOperation.difference,
      Path()..addRect(Offset.zero & size),
      Path()..addRRect(RRect.fromRectAndRadius(rect, const Radius.circular(16))),
    );
    canvas.drawPath(
      mask,
      Paint()..color = Colors.black.withValues(alpha: 0.45),
    );

    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 4
      ..strokeCap = StrokeCap.round
      ..color = AppColors.primary;
    const arm = 34.0;
    final r = rect;
    canvas.drawLine(r.topLeft, r.topLeft + const Offset(arm, 0), paint);
    canvas.drawLine(r.topLeft, r.topLeft + const Offset(0, arm), paint);
    canvas.drawLine(r.topRight, r.topRight + const Offset(-arm, 0), paint);
    canvas.drawLine(r.topRight, r.topRight + const Offset(0, arm), paint);
    canvas.drawLine(r.bottomLeft, r.bottomLeft + const Offset(arm, 0), paint);
    canvas.drawLine(r.bottomLeft, r.bottomLeft + const Offset(0, -arm), paint);
    canvas.drawLine(r.bottomRight, r.bottomRight + const Offset(-arm, 0), paint);
    canvas.drawLine(r.bottomRight, r.bottomRight + const Offset(0, -arm), paint);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class _EquipmentPicker extends StatefulWidget {
  const _EquipmentPicker({required this.api});

  final ApiService api;

  @override
  State<_EquipmentPicker> createState() => _EquipmentPickerState();
}

class _EquipmentPickerState extends State<_EquipmentPicker> {
  late Future<List<Equipment>> _future;

  @override
  void initState() {
    super.initState();
    _future = widget.api.fetchEquipmentList();
  }

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 32,
                height: 4,
                decoration: BoxDecoration(
                  color: palette.outlineStrong,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            const SizedBox(height: 14),
            Text(
              'Выберите оборудование',
              style: TextStyle(
                color: palette.text,
                fontSize: 20,
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: 8),
            Flexible(
              child: SizedBox(
                height: MediaQuery.sizeOf(context).height * 0.5,
                child: FutureBuilder<List<Equipment>>(
                  future: _future,
                  builder: (context, snapshot) {
                    if (snapshot.connectionState != ConnectionState.done) {
                      return const Center(child: CircularProgressIndicator());
                    }
                    if (snapshot.hasError) {
                      return Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.cloud_off,
                                color: palette.yellow, size: 40),
                            const SizedBox(height: 10),
                            Text(
                              snapshot.error is ApiException
                                  ? (snapshot.error as ApiException).message
                                  : 'Не удалось загрузить список',
                              textAlign: TextAlign.center,
                              style: TextStyle(
                                color: palette.textSecondary,
                                fontSize: 14,
                              ),
                            ),
                            const SizedBox(height: 12),
                            OutlinedButton(
                              onPressed: () => setState(() {
                                _future = widget.api.fetchEquipmentList();
                              }),
                              child: const Text('Повторить'),
                            ),
                          ],
                        ),
                      );
                    }
                    final items = snapshot.data ?? const <Equipment>[];
                    if (items.isEmpty) {
                      return Center(
                        child: Text(
                          'Список оборудования пуст',
                          style: TextStyle(
                            color: palette.textMuted,
                            fontSize: 14,
                          ),
                        ),
                      );
                    }
                    return ListView.separated(
                      itemCount: items.length,
                      separatorBuilder: (_, _) =>
                          Divider(color: palette.outline),
                      itemBuilder: (context, index) {
                        final equipment = items[index];
                        return ListTile(
                          contentPadding: const EdgeInsets.symmetric(
                            horizontal: 4,
                            vertical: 4,
                          ),
                          leading: Icon(Icons.precision_manufacturing,
                              color: palette.accent),
                          title: Text(
                            equipment.name,
                            style: TextStyle(
                              color: palette.text,
                              fontWeight: FontWeight.w700,
                              fontSize: 16,
                            ),
                          ),
                          subtitle: Text(
                            '${equipment.id}'
                            '${equipment.location == null ? '' : ' · ${equipment.location}'}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              color: palette.textMuted,
                              fontSize: 12,
                            ),
                          ),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () =>
                              Navigator.of(context).pop(equipment.id),
                        );
                      },
                    );
                  },
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
