/*
 * traba_toma — ESP32. Traba el pico de UN equipo de abastecimiento.
 *
 * POLARIDAD, que es lo unico que hay que entender de este archivo:
 *   solenoide DESENERGIZADO = pico LIBRE = como se trabaja hoy.
 *   solenoide ENERGIZADO    = pico TRABADO.
 * O sea que el sistema solo puede AGREGAR una restriccion, nunca sacarla.
 * Sin luz, sin wifi, sin server o con este firmware colgado, el pico queda
 * libre y la operacion queda exactamente igual de segura que antes de existir
 * este aparato. Un error nuestro cuesta una parada al pedo, nunca una carga
 * equivocada.
 *
 * NO INVIERTAS ESTA LOGICA. Si algun dia el solenoide se cablea al reves
 * (energizado = libre), un corte de luz traba todos los picos de la aeroplanta
 * y alguien va a puentear el rele esa misma noche — y ahi perdiste el interlock
 * para siempre.
 *
 * CICLO: el ESP pregunta, nadie le empuja. Asi no hay que abrir ningun puerto
 * en la red de rampa ni mantener un broker. Consulta cada POLL_MS y aplica un
 * watchdog: si pasan WATCHDOG_MS sin una respuesta buena, LIBERA.
 *
 * RED: se conecta al hotspot de la tablet del operario y sale a internet contra
 * el server en Render. Si el operario se lleva la tablet, el watchdog libera.
 *
 * OVERRIDE: el boton fisico libera y queda liberado hasta el proximo reinicio.
 * Siempre gana sobre el server. Tiene que estar al alcance del operador: un
 * interlock del que no se pueda salir a mano es un interlock que se puentea.
 *
 * ponytail: setInsecure() — no valida el certificado del server. El daño de un
 * MITM esta acotado por la polaridad: lo peor que puede hacer es suprimir una
 * traba (dejar todo como hoy) o trabar un equipo (ruidoso, se nota al toque).
 * Si algun dia importa, pineá el fingerprint del certificado de Render.
 *
 * Placa: ESP32. Para ESP8266 cambiar WiFi.h por ESP8266WiFi.h y HTTPClient por
 * ESP8266HTTPClient.h; el resto es igual.
 */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

#include "secrets.h"  // WIFI_SSID, WIFI_PASS, TOMA_HOST, TOMA_TOKEN, EQUIPO_CODIGO

// --- Pines ---
const int PIN_SOLENOIDE = 26;  // a la base del MOSFET / bobina del rele
const int PIN_LED_ROJO  = 25;  // encendido = trabado
const int PIN_BOTON     = 27;  // override a masa, con INPUT_PULLUP

// --- Tiempos ---
const unsigned long POLL_MS     = 2000;   // cada cuanto pregunta
const unsigned long WATCHDOG_MS = 10000;  // sin respuesta buena por mas de esto -> libera

unsigned long ultimaRespuestaOk = 0;
bool trabado = false;
bool overrideManual = false;

void aplicar(bool nuevoEstado) {
  trabado = nuevoEstado;
  digitalWrite(PIN_SOLENOIDE, trabado ? HIGH : LOW);
  digitalWrite(PIN_LED_ROJO, trabado ? HIGH : LOW);
}

void setup() {
  // Lo PRIMERO: dejar el pico libre. Si el firmware se cuelga mas adelante,
  // el estado en el que quedo es el seguro.
  pinMode(PIN_SOLENOIDE, OUTPUT);
  pinMode(PIN_LED_ROJO, OUTPUT);
  aplicar(false);

  pinMode(PIN_BOTON, INPUT_PULLUP);
  Serial.begin(115200);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("\n[traba_toma] equipo %s, conectando a %s\n", EQUIPO_CODIGO, WIFI_SSID);
  ultimaRespuestaOk = millis();
}

// Devuelve true si pudo consultar. Deja el resultado en *traba.
bool consultar(bool *traba) {
  if (WiFi.status() != WL_CONNECTED) return false;

  WiFiClientSecure cliente;
  cliente.setInsecure();  // ver el comentario de arriba sobre el ceiling
  cliente.setTimeout(5);

  HTTPClient http;
  String url = String("https://") + TOMA_HOST + "/toma/estado?equipo=" + EQUIPO_CODIGO;
  if (!http.begin(cliente, url)) return false;

  http.addHeader("X-Toma-Token", TOMA_TOKEN);
  http.setTimeout(5000);
  int codigo = http.GET();

  bool ok = false;
  if (codigo == 200) {
    String cuerpo = http.getString();
    // Sin libreria de JSON a proposito: la respuesta es nuestra y tiene un solo
    // campo que importa. Menos codigo en el aparato es menos para que falle.
    *traba = (cuerpo.indexOf("\"traba\":true") >= 0) ||
             (cuerpo.indexOf("\"traba\": true") >= 0);
    ok = true;
  } else {
    // 401 incluido: si el token esta mal, NO trabamos. Fallar cerrado aca
    // significaria trabar picos por un error de configuracion.
    Serial.printf("[traba_toma] HTTP %d\n", codigo);
  }
  http.end();
  return ok;
}

void loop() {
  // El boton fisico gana siempre y no se puede volver atras sin reiniciar.
  if (digitalRead(PIN_BOTON) == LOW) {
    if (!overrideManual) {
      Serial.println("[traba_toma] OVERRIDE MANUAL: pico liberado hasta reiniciar");
      overrideManual = true;
    }
    aplicar(false);
    delay(200);
    return;
  }

  bool quiereTrabar = false;
  if (consultar(&quiereTrabar)) {
    ultimaRespuestaOk = millis();
    if (quiereTrabar != trabado) {
      Serial.printf("[traba_toma] %s\n", quiereTrabar ? "TRABANDO" : "liberando");
    }
    aplicar(quiereTrabar);
  } else if (millis() - ultimaRespuestaOk > WATCHDOG_MS) {
    // Perdimos contacto. Liberar: que el sistema se caiga nunca puede dejar un
    // pico trabado.
    if (trabado) Serial.println("[traba_toma] WATCHDOG: sin server, liberando");
    aplicar(false);
  }

  delay(POLL_MS);
}
