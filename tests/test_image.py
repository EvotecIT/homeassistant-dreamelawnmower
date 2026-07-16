from io import BytesIO

from PIL import Image

from custom_components.dreame_lawn_mower.image import png_bytes_to_jpeg


def test_png_bytes_to_jpeg_applies_clockwise_display_rotation() -> None:
    source = BytesIO()
    image = Image.new("RGB", (40, 20), (255, 0, 0))
    image.paste((0, 0, 255), (20, 0, 40, 20))
    image.save(source, format="PNG")

    converted = png_bytes_to_jpeg(source.getvalue(), rotation=90)

    with Image.open(BytesIO(converted)) as image:
        assert image.size == (20, 40)
        top = image.getpixel((10, 5))
        bottom = image.getpixel((10, 35))
        assert top[0] > top[2]
        assert bottom[2] > bottom[0]
