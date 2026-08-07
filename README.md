# Agenda de Entregas — Courier

App tipo Calendly para que tus clientes agenden recojo en Real Plaza o envío con motorizado. Cada servicio tiene su propio horario, independiente del otro.

## Cómo correrla en tu computadora

1. Abre una terminal en esta carpeta.
2. Instala las dependencias:
   ```
   pip3 install -r requirements.txt
   ```
3. Inicia la app:
   ```
   python3 app.py
   ```
4. Abre en tu navegador:
   - Formulario de reserva: http://127.0.0.1:5000/
   - Panel de reservas: http://127.0.0.1:5000/admin

La primera vez se crea automáticamente `agenda.db` con la base de datos de reservas.

## Cómo establecer tus horarios (config.json)

Cada servicio en `event_types` tiene su propio bloque `business_hours`, así que Real Plaza y motorizado pueden tener días y horas totalmente distintos.

Ejemplo incluido:
- **Envío con motorizado**: lunes a viernes 9:00–15:00, sábado 9:00–13:00.
- **Recojo en Real Plaza**: solo lunes, miércoles y viernes, 17:00–19:00.

Las claves de día van de `0` (lunes) a `6` (domingo). Si un día no aparece en el `business_hours` de un servicio, ese servicio no tiene horarios disponibles ese día — así puedes, por ejemplo, dejar Real Plaza abierto un solo día a la semana sin afectar el horario de motorizado.

Otros campos por servicio:
- `capacity_per_slot`: cuántas reservas simultáneas admite ese horario (ej. 3 recojos a la vez, pero 1 solo motorizado).
- `requires_address`: si pide dirección obligatoria (activado en envío, desactivado en recojo).
- `slot_duration_minutes` (nivel raíz): cada cuántos minutos hay un horario, aplica a ambos servicios.

Guarda el archivo y reinicia `python3 app.py` para que tome los cambios.

## Cómo funciona

- El cliente elige el servicio, ve solo las fechas/horas válidas para ESE servicio, y llena sus datos.
- La app bloquea automáticamente horarios llenos — no hay dobles reservas.
- Vos ves todo en `/admin`, con opción de cancelar cualquier reserva.

## Publicarla para tus clientes (Render, gratis)

1. Sube esta carpeta a un repositorio de GitHub.
2. Crea cuenta gratis en render.com.
3. **New +** → **Web Service** → conecta el repo.
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `gunicorn app:app`
6. Instance Type: **Free** → **Create Web Service**.

Te da un link tipo `tunegocio.onrender.com` para compartir con tus clientes. En el plan gratis, la app duerme tras 15 min sin uso y tarda unos segundos en despertar con la primera visita.
