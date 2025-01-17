import cv2
import numpy as np
from datetime import datetime, timezone
from PIL import ImageFont, ImageDraw, Image
import qrcode
import textwrap
import re
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import io
import base64
import os
import logging
import sys

here = os.path.abspath(os.path.dirname(__file__))

y_draw = 20

import boto3
from botocore.exceptions import NoCredentialsError, ClientError

if os.getenv("LOG_LEVEL"):
    log_level = os.getenv("LOG_LEVEL")
else:
    log_level = "CRITICAL"

numeric_level = getattr(logging, log_level.upper(), None)
if not isinstance(numeric_level, int):
    raise ValueError(f"Invalid log level: {log_level}")

logging.basicConfig(
    level=numeric_level,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout
)

def update_dynamodb_table(timename, timestamp, name, username, table_name="github-basic"):
    # Create a DynamoDB resource
    dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
    
    # Get the table
    table = dynamodb.Table(table_name)
    
    try:
        # Update the item in the table
        response = table.update_item(
            Key={
                'timename': timename
            },
            UpdateExpression="SET #time = :time, #name = :name, #username = :username",
            ExpressionAttributeNames={
                '#time': 'time',
                '#name': 'name',
                '#username': 'username'
            },
            ExpressionAttributeValues={
                ':time': timestamp,
                ':name': name,
                ':username': username
            },
            ReturnValues="UPDATED_NEW"
        )
        
        logging.debug("UpdateItem succeeded:")
        logging.debug(response)
        return response
    
    except ClientError as e:
        logging.debug(f"Error updating item: {e.response['Error']['Message']}")
        return None
    except NoCredentialsError:
        logging.debug("Credentials not available")
        return None

# Function to load a private key from an environment variable
def load_private_key_from_env():
    # Get the private key from the environment variable

    privkey = os.getenv("PRIVKEY_BASE64")
    if privkey is None:
        raise Exception("PRIVKEY_BASE64 env var not found")
    private_key_pem = base64.b64decode(privkey).decode('utf-8')
    
    # Load the private key
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode(),  # Convert string to bytes
        password=None,  # Add a password if the key is encrypted
        backend=default_backend()
    )
    return private_key

# Step 1: Load the PNG image
def sign_image(image_path):
    image = Image.open(image_path)

    # Convert the image to bytes
    image_bytes = io.BytesIO()
    image.save(image_bytes, format="WEBP")
    image_data = image_bytes.getvalue()

    private_key = load_private_key_from_env()

    # Sign the image data
    signature = private_key.sign(
        image_data,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )

    # Step 3: Embed the signature in the image metadata
    # Convert the signature to a hex string for storage
    signature_hex = signature.hex()

    # Add the signature to the image's metadata
    image.info["signature"] = signature_hex

    # Save the image with the embedded signature
    signed_image_path = image_path
    image.save(signed_image_path, format="WEBP")

    logging.debug(f"Image signed and saved successfully to '{signed_image_path}'!")
    logging.debug(f"Signature embedded in metadata: {signature_hex}")

def break_into_lines(text, max_length=20):
    # Define a regex pattern to split on punctuation (except apostrophes and other pronunciation marks)
    pattern = r'[\s,.;:!?\-—]|\\x[0-9A-Fa-f]{2}'
    # Split the text into parts based on the pattern
    parts = re.split(pattern, text)
    
    # Reconstruct the text with spaces around punctuation for proper wrapping
    reconstructed_text = ' '.join([part if part.strip() else ' ' for part in parts])
    
    # Use textwrap to wrap the text into lines of max_length
    wrapped_lines = textwrap.wrap(reconstructed_text, width=max_length, break_long_words=False)
    
    return wrapped_lines

def drawTextCenteredFit(draw, image_size, text, font_path, fill, max_width=550, max_height=300, y_center=250, font_size= 180):
    og_font_size = font_size
    spacing = font_size/18  # Spacing between lines
    lines = []

    # Break text into lines
    for line in break_into_lines(text, max_length=15 + len(text)/5):
        lines.append({"content": line})
    logging.debug(lines)
    # Adjust font size until the text fits within the bounds
    while True:
        font = ImageFont.truetype(font_path, font_size)
        total_height = 0
        max_line_width = 0

        # Calculate the total height and maximum line width
        for line in lines:
            bbox = font.getbbox(line["content"])
            line["width"] = bbox[2] - bbox[0]
            line["height"] = bbox[3] - bbox[1]
            total_height += line["height"] + spacing
            if line["width"] > max_line_width:
                max_line_width = line["width"]

        # Check if the text fits within the bounds
        if max_line_width <= max_width and total_height <= max_height:
            break
        else:
            # Reduce the font size and try again
            font_size -= 1
            if font_size < 1:
                raise ValueError("Text cannot fit within the specified bounds.")
            else:
                spacing = 10*font_size/og_font_size

    # Calculate the starting y-position for vertical centering
    total_height -= spacing  # Remove the last spacing
    ypos = y_center - (total_height / 2)

    # Draw the text line by line
    for line in lines:
        bbox = font.getbbox(line["content"])
        x = (image_size - line["width"]) // 2  # Center horizontally
        draw.text((x, ypos), line["content"], font=font, fill=fill)
        ypos += line["height"] + spacing  # Add spacing between lines

def drawTextCentered(draw, image_size, text, font, fill):
    global y_draw
    """Draw text centered horizontally on the given y-coordinate."""
    # Use font.getbbox for text dimensions
    bbox = font.getbbox(text)
    text_width = bbox[2] - bbox[0]  # Calculate width from bounding box
    text_height = bbox[3] - bbox[1]  # Calculate height from bounding box
    x = (image_size - text_width) // 2  # Calculate horizontal center
    draw.text((x, y_draw), text, font=font, fill=fill)
    y_draw += text_height + 20

def generate_hex(image_size, font_large):
    global y_draw
    # Create a blank transparent image (RGBA)
    image = np.zeros((image_size, image_size, 4), dtype=np.uint8)

    # Define the center and radius for the hexagon
    center = (image_size // 2, image_size // 2)
    radius = image_size//2.05

    # Calculate the vertices of the hexagon
    hexagon = []
    for i in range(6):
        angle = np.deg2rad(i * 60)  # Convert angle to radians
        x = int(center[0] + radius * np.cos(angle))
        y = int(center[1] + radius * np.sin(angle))
        hexagon.append((x, y))
    hexagon = np.array(hexagon, dtype=np.int32)  # Convert to numpy array

    # Create a mask for the hexagon
    mask = np.zeros((image_size, image_size), dtype=np.uint8)
    cv2.fillPoly(mask, [hexagon], color=255)

    # Generate a gradient
    gradient_start_color = np.array([255, 100, 50, 255])  # RGBA start color
    gradient_end_color = np.array([50, 150, 255, 255])    # RGBA end color
    gradient_end_color = np.array([0, 0, 0, 255])    # RGBA end color

    # Get coordinates of the masked points
    mask_points = np.where(mask > 0)
    y_coords, x_coords = mask_points

    # Normalize y-coordinates for gradient interpolation
    alpha = y_coords / float(image_size)

    # Interpolate gradient colors
    gradient_colors = (
        (1 - alpha[:, None]) * gradient_start_color + alpha[:, None] * gradient_end_color
    ).astype(np.uint8)

    # Assign gradient colors to the hexagon in the image
    image[y_coords, x_coords] = gradient_colors

    # Draw the hexagon outline
    cv2.polylines(image, [hexagon], isClosed=True, color=(255, 255, 255, 255), thickness=13)

    # Convert image to PIL format for text addition
    pil_image = Image.fromarray(image)
    draw = ImageDraw.Draw(pil_image)

    y_draw = int(image_size*0.40)
    drawTextCentered(draw, image_size, "Version Control", font_large, (255, 255, 255, 255))
    drawTextCentered(draw, image_size, "Basic Proficiency", font_large, (255, 255, 255, 255))

    return pil_image, draw

def generate_badge(name, username, tablename):

    time = datetime.now(timezone.utc)  # Get the current time in UTC
    timestamp = time.strftime("%Y-%m-%d_%H:%M:%S_%Z")  # Include timezone name

    global y_draw
    y_draw = 30
    image_size = 1920

    font_path = "./nofile"  # Adjust font path as needed
    font_large = ImageFont.truetype(font_path, image_size//10)
    font_medium = ImageFont.truetype(font_path, image_size/16)
    font_small = ImageFont.truetype(font_path, image_size/60)

    pil_image, draw = generate_hex(image_size, font_large)
    
    # Sanitize the name to be file-friendly
    safe_name = re.sub(r'[^\w\-_. ]', '_', name).replace(' ', '_')  # Replace invalid characters with '_'
    safe_name = safe_name[:50]
    timename = (f"{timestamp}_{safe_name}")[:255]
    output_file = os.path.join(here, "output", f"{timename}_badge.webp").replace(":","-")
    os.makedirs(os.path.join(here, "output"), exist_ok=True)
    logging.debug(output_file)
    

    # Add certification text
    drawTextCenteredFit(draw = draw, image_size=image_size, text=name, font_path=font_path, fill=(255, 255, 255, 255), y_center=int(image_size*0.22), font_size=image_size//3, max_height=image_size*0.2, max_width=image_size*0.55)
    y_draw = int(image_size*0.345)
    drawTextCentered(draw, image_size, f"({username})", font_small, (255, 255, 255, 255))
    y_draw = int(image_size*0.365)
    drawTextCentered(draw, image_size, "has been awarded the", font_small, (255, 255, 255, 255))

    # Add timestamp and signature
    y_draw = int(image_size*0.606)
    drawTextCentered(draw, image_size, f"certification on {timestamp}", font_small, (255, 255, 255, 255))

    # Generate QR code
    verify_url = f"https://codecollective.us/verify.html?timename={timename}&tablename={tablename}"
    logging.debug(f"Verify URL: {verify_url}")
    qr = qrcode.QRCode(box_size=10, border=1)
    qr.add_data(verify_url)
    qr.make(fit=True)
    qr_image = qr.make_image(fill="black", back_color="white").convert("RGBA")

    # Insert QR code into the badge
    qr_len = int(image_size//4.7)
    qr_image = qr_image.resize((qr_len, qr_len))
    y_draw = int(image_size*0.64)
    pil_image.paste(qr_image, (int(image_size//2-qr_len//2), int(y_draw)), qr_image)

    y_draw = int(image_size * 0.865)
    drawTextCentered(draw, image_size, "This image is cryptographically signed by Code Collective", font_small, (255, 255, 255, 255))
    y_draw -= 17
    drawTextCentered(draw, image_size, "It can also be verified by scanning the QR code above", font_small, (255, 255, 255, 255))

    # Save the final image
    final_image = np.array(pil_image)
    cv2.imwrite(output_file, final_image)

    logging.debug(f"Badge with text and QR code saved to {output_file}")
    sign_image(output_file)
    logging.debug("Image Signed")

    logging.debug("Updating DB")
    update_dynamodb_table(timename=timename, timestamp=timestamp, name=name, username=username, table_name=tablename)
    logging.debug("DB Updated")

    print(output_file)
    return output_file

# Function to get a value from either command-line arguments or environment variables
def get_arg_or_env(index, env_name):
    if len(sys.argv) > index:
        os.environ[env_name] = sys.argv[index]
        return sys.argv[index]
    return os.getenv(env_name)


if __name__ == "__main__":
    full_name = sys.argv[1]
    username = sys.argv[2]
    badge_type = sys.argv[3]

    PRIVKEY_BASE64 = get_arg_or_env(4, "PRIVKEY_BASE64")
    AWS_ACCESS_KEY_ID = get_arg_or_env(5, "AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = get_arg_or_env(6, "AWS_SECRET_ACCESS_KEY")
    AWS_DEFAULT_REGION = get_arg_or_env(7, "AWS_DEFAULT_REGION")

    generate_badge(full_name, username, badge_type)
    #generate_badge("Julian Coy", "julianfl0w", 'github-basic')
    #generate_badge("Julian Coy Loiacono", "julianfl0w", 'github-basic')
    #generate_badge("Nicholas If-Jesus-Christ-had-not-died-for-thee-thou-hadst-been-damned Barebone", "cyberbone", 'github-basic')
    #generate_badge("Pablo Diego José Francisco de Paula Juan Nepomuceno María de los Remedios Cipriano de la Santísima Trinidad Martyr Patricio Clito Ruíz y Picasso", "PabloSlays", 'github-basic')
    
    # generate blank badge
    if False:
        font_path = "./nofile"  # Adjust font path as needed
        pil_image, draw = generate_hex(image_size=1920, font_large=ImageFont.truetype(font_path, 1920//10))
        # Save the final image
        final_image = np.array(pil_image)
        cv2.imwrite("github-basic.webp", final_image)