import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:ntec_inspector/services/frame_codec.dart';
import 'package:ntec_inspector/widgets/overlay_mapping.dart';
import 'package:material_ui/material_ui.dart';

Uint8List _makeJpeg(int width, int height) {
  final picture = img.Image(width: width, height: height, numChannels: 3);
  img.fill(picture, color: img.ColorRgb8(90, 120, 160));
  return img.encodeJpg(picture, quality: 85);
}

void main() {
  test('фото 4032×3024: пропорции сохранены, длинная сторона 1600', () async {
    final source = _makeJpeg(4032, 3024);
    final prepared = await prepareUploadedPhoto(source);
    final decoded = img.decodeImage(prepared);
    expect(decoded, isNotNull);
    expect(decoded!.width, 1600);
    expect(decoded.height, 1200);
    expect(decoded.width / decoded.height, closeTo(4 / 3, 1e-6));
  });

  test('фото 3024×4032: вертикаль не переворачивается и не сплющивается',
      () async {
    final source = _makeJpeg(3024, 4032);
    final prepared = await prepareUploadedPhoto(source);
    final decoded = img.decodeImage(prepared);
    expect(decoded, isNotNull);
    expect(decoded!.width, 1200);
    expect(decoded.height, 1600);
    expect(decoded.width / decoded.height, closeTo(3 / 4, 1e-6));
  });

  test('маленькое фото 640×480 не апскейлится', () async {
    final source = _makeJpeg(640, 480);
    final prepared = await prepareUploadedPhoto(source);
    final decoded = img.decodeImage(prepared);
    expect(decoded, isNotNull);
    expect(decoded!.width, 640);
    expect(decoded.height, 480);
  });

  test('cover: кадр 480×720, виджет 1080×1920', () {
    final mapping = OverlayMapping.forFit(
      viewport: const Size(1080, 1920),
      frame: const Size(480, 720),
      fit: BoxFit.cover,
    );
    expect(mapping.scale, closeTo(2.6666667, 1e-6));
    expect(mapping.offset.dx, closeTo(-100, 1e-6));
    expect(mapping.offset.dy, closeTo(0, 1e-6));
    expect(mapping.toScreen(<double>[0, 0]), const Offset(-100, 0));
    expect(mapping.toScreen(<double>[480, 720]), const Offset(1180, 1920));
    expect(mapping.toScreen(<double>[240, 360]), const Offset(540, 960));
  });

  test('cover: кадр 720×480, виджет 1920×1080', () {
    final mapping = OverlayMapping.forFit(
      viewport: const Size(1920, 1080),
      frame: const Size(720, 480),
      fit: BoxFit.cover,
    );
    expect(mapping.scale, closeTo(2.6666667, 1e-6));
    expect(mapping.offset.dx, closeTo(0, 1e-6));
    expect(mapping.offset.dy, closeTo(-100, 1e-6));
    expect(mapping.toScreen(<double>[0, 0]), const Offset(0, -100));
    expect(mapping.toScreen(<double>[720, 480]), const Offset(1920, 1180));
    expect(mapping.toScreen(<double>[360, 240]), const Offset(960, 540));
  });

  test('letterbox: оверлеи пересчитываются по фактическому размеру фото', () {
    final mapping = OverlayMapping.forFit(
      viewport: const Size(400, 400),
      frame: const Size(1600, 1200),
      fit: BoxFit.contain,
    );
    // Картинка 4:3 в квадрате: масштаб 0.25, поля сверху/снизу по 50 px.
    expect(mapping.scale, closeTo(0.25, 1e-9));
    expect(mapping.offset.dx, closeTo(0, 1e-9));
    expect(mapping.offset.dy, closeTo(50, 1e-9));
    expect(mapping.toScreen(<double>[0, 0]), const Offset(0, 50));
    expect(mapping.toScreen(<double>[1600, 1200]), const Offset(400, 350));
    expect(mapping.toScreen(<double>[800, 600]), const Offset(200, 200));

    final inverse = mapping.toFrame(const Offset(200, 200));
    expect(inverse.dx, closeTo(800, 1e-9));
    expect(inverse.dy, closeTo(600, 1e-9));
  });

  test('letterbox с нижней панелью: видимая область и смещение', () {
    final mapping = OverlayMapping.forFit(
      viewport: const Size(360, 460),
      frame: const Size(1600, 1200),
      fit: BoxFit.contain,
    );
    expect(mapping.scale, closeTo(0.225, 1e-9));
    expect(mapping.offset.dy, closeTo(95, 1e-9));
    expect(mapping.toScreen(<double>[0, 0]), const Offset(0, 95));
  });
}
