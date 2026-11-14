import 'package:material_ui/material_ui.dart';

import '../models/inspection_event.dart';
import '../theme/app_theme.dart';

Color eventSeverityColor(String severity, [AppPalette? palette]) {
  final p = palette ?? AppPalette.dark;
  switch (severity) {
    case 'critical':
      return p.red;
    case 'warning':
      return p.yellow;
    default:
      return p.accent;
  }
}

class InspectionEventTile extends StatelessWidget {
  const InspectionEventTile({super.key, required this.event, this.dense = false});

  final InspectionEvent event;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    final color = eventSeverityColor(event.severity, palette);
    return Padding(
      padding: EdgeInsets.symmetric(vertical: dense ? 5 : 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 58,
            child: Text(
              event.timeLabel,
              style: TextStyle(
                color: palette.textMuted,
                fontSize: 12,
              ),
            ),
          ),
          SizedBox(
            width: 50,
            child: Text(
              event.frameId == null ? '—' : '#${event.frameId}',
              style: TextStyle(
                color: palette.textMuted,
                fontSize: 12,
              ),
            ),
          ),
          Container(
            width: 6,
            height: 6,
            margin: const EdgeInsets.only(top: 5, right: 6),
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  eventTypeLabel(event.type),
                  style: TextStyle(
                    color: color,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 1),
                Text(
                  event.message,
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: palette.text,
                    fontSize: 12,
                    height: 1.3,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Шторка журнала событий на AR-экране.
class InspectionJournalSheet extends StatelessWidget {
  const InspectionJournalSheet({super.key, required this.log});

  final InspectionEventLog log;

  @override
  Widget build(BuildContext context) {
    final palette = AppPalette.of(context);
    return AnimatedBuilder(
      animation: log,
      builder: (context, _) {
        final events = log.events;
        return SizedBox(
          height: MediaQuery.sizeOf(context).height * 0.65,
          child: SafeArea(
            top: false,
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 10, 8, 8),
                  child: Row(
                    children: [
                      Icon(Icons.receipt_long_outlined,
                          size: 18, color: palette.accent),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'Журнал событий',
                          style: TextStyle(
                            color: palette.text,
                            fontSize: 17,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                      Text(
                        '${events.length}',
                        style: TextStyle(
                          color: palette.textMuted,
                          fontSize: 12,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      IconButton(
                        tooltip: 'Закрыть',
                        onPressed: () => Navigator.of(context).pop(),
                        icon: const Icon(Icons.close, size: 20),
                      ),
                    ],
                  ),
                ),
                Divider(color: palette.outline, height: 1),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 6, 16, 4),
                  child: Row(
                    children: [
                      SizedBox(
                        width: 58,
                        child: Text(
                          'ВРЕМЯ',
                          style: TextStyle(
                            color: palette.textMuted,
                            fontSize: 12,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 0.5,
                          ),
                        ),
                      ),
                      SizedBox(
                        width: 50,
                        child: Text(
                          'КАДР',
                          style: TextStyle(
                            color: palette.textMuted,
                            fontSize: 12,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 0.5,
                          ),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          'СОБЫТИЕ',
                          style: TextStyle(
                            color: palette.textMuted,
                            fontSize: 12,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 0.5,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                Expanded(
                  child: events.isEmpty
                      ? Center(
                          child: Text(
                            'Событий пока нет',
                            style: TextStyle(
                              color: palette.textMuted,
                              fontSize: 13,
                            ),
                          ),
                        )
                      : ListView.separated(
                          padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                          itemCount: events.length,
                          separatorBuilder: (_, _) =>
                              Divider(color: palette.outline, height: 1),
                          itemBuilder: (context, index) =>
                              InspectionEventTile(event: events[index]),
                        ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
