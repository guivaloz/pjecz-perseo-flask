"""
Nóminas, generadores de dispersiones de pensionados
"""

from datetime import datetime
from pathlib import Path

import pytz
from openpyxl import Workbook

from config.settings import get_settings
from lib.exceptions import (
    MyBucketNotFoundError,
    MyEmptyError,
    MyFileNotAllowedError,
    MyFileNotFoundError,
    MyNotValidParamError,
    MyUploadError,
)
from lib.google_cloud_storage import upload_file_to_gcs
from perseo.blueprints.nominas.generators.common import (
    GCS_BASE_DIRECTORY,
    LOCAL_BASE_DIRECTORY,
    TIMEZONE,
    actualizar_quincena_producto,
    bitacora,
    consultar_validar_quincena,
)
from perseo.blueprints.nominas.models import Nomina
from perseo.blueprints.personas.models import Persona

FUENTE = "DISPERSIONES PENSIONADOS"


def crear_dispersiones_pensionados(
    quincena_clave: str,
    quincena_producto_id: int,
    tipo: str = "SALARIO",
) -> str:
    """Crear archivo XLSX con las dispersiones pensionados de una quincena"""

    # Validar el tipo
    if tipo not in ["SALARIO", "AGUINALDO"]:
        raise MyNotValidParamError(f"El tipo {tipo} no es valido")

    # Consultar y validar quincena
    quincena = consultar_validar_quincena(quincena_clave)  # Puede provocar una excepcion

    # Mandar mensaje de inicio a la bitacora
    bitacora.info("Inicia crear dispersiones pensionados %s %s", quincena_clave, tipo)

    # Consultar las nominas de la quincena
    nominas = (
        Nomina.query.join(Persona)
        .filter(Nomina.quincena_id == quincena.id)
        .filter(Nomina.tipo == tipo)
        .filter(Nomina.estatus == "A")
        .order_by(Persona.rfc)
        .all()
    )

    # Si no hay nominas, provocar error y salir
    if len(nominas) == 0:
        mensaje = f"No hay registros en nominas de tipo {tipo}"
        actualizar_quincena_producto(quincena_producto_id, quincena.id, FUENTE, [mensaje])
        raise MyEmptyError(mensaje)

    # Iniciar el archivo XLSX
    libro = Workbook()

    # Tomar la hoja del libro XLSX
    hoja = libro.active

    # Agregar la fila con las cabeceras de las columnas
    hoja.append(
        [
            "CONSECUTIVO",
            "FORMA DE PAGO",
            "TIPO DE CUENTA",
            "BANCO RECEPTOR",
            "CUENTA ABONO",
            "IMPORTE PAGO",
            "CLAVE BENEFICIARIO",
            "RFC",
            "NOMBRE",
            "REFERENCIA PAGO",
            "CONCEPTO PAGO",
        ]
    )

    # Bucle para crear cada fila del archivo XLSX
    contador = 0
    personas_sin_cuentas = []
    for nomina in nominas:
        # Si el modelo de la persona NO es 3, se omite
        if nomina.persona.modelo != 3:
            continue

        # Tomar las cuentas de la persona
        cuentas = nomina.persona.cuentas

        # Si no tiene cuentas, entonces se le crea una cuenta con el banco 10
        if len(cuentas) == 0:
            personas_sin_cuentas.append(nomina.persona)
            continue

        # Tomar la cuenta de la persona que no tenga la clave 9, porque esa clave es la de DESPENSA
        su_cuenta = None
        for cuenta in cuentas:
            if cuenta.banco.clave != "9" and cuenta.estatus == "A":
                su_cuenta = cuenta
                break

        # Si no tiene cuenta bancaria, entonces se le crea una cuenta con el banco 10
        if su_cuenta is None:
            personas_sin_cuentas.append(nomina.persona)
            continue

        # Definir referencia_pago, se forma con los dos ultimos caracteres y los caracteres tercero y cuarto de la quincena
        referencia_pago = f"{quincena_clave[-2:]}{quincena_clave[2:4]}"

        # Definir concepto_pago, se forma con el texto "QUINCENA {dos digitos} PENSIONADOS"
        concepto_pago = f"QUINCENA {quincena_clave[-2:]} PENSIONADOS"

        # Agregar la fila
        hoja.append(
            [
                contador + 1,
                "04",
                "9",
                su_cuenta.banco.clave_dispersion_pensionados,
                su_cuenta.num_cuenta,
                nomina.importe,
                contador + 1,
                nomina.persona.rfc,
                nomina.persona.nombre_completo,
                referencia_pago,
                concepto_pago,
            ]
        )

        # Incrementar contador
        contador += 1

    # Si el contador es cero, provocar error
    if contador == 0:
        mensaje = "No hubo filas que agregar al archivo XLSX"
        actualizar_quincena_producto(quincena_producto_id, quincena.id, FUENTE, [mensaje])
        raise MyEmptyError(mensaje)

    # Determinar el nombre del archivo XLSX
    ahora = datetime.now(tz=pytz.timezone(TIMEZONE))
    nombre_archivo_xlsx = f"dispersiones_pensionados_{quincena_clave}_{ahora.strftime('%Y-%m-%d_%H%M%S')}.xlsx"

    # Determinar las rutas con directorios con el año y el número de mes en dos digitos
    ruta_local = Path(LOCAL_BASE_DIRECTORY, ahora.strftime("%Y"), ahora.strftime("%m"))
    ruta_gcs = Path(GCS_BASE_DIRECTORY, ahora.strftime("%Y"), ahora.strftime("%m"))

    # Si no existe el directorio local, crearlo
    Path(ruta_local).mkdir(parents=True, exist_ok=True)

    # Guardar el archivo XLSX
    ruta_local_archivo_xlsx = str(Path(ruta_local, nombre_archivo_xlsx))
    libro.save(ruta_local_archivo_xlsx)

    # Si esta configurado Google Cloud Storage
    mensaje_gcs = ""
    public_url = ""
    settings = get_settings()
    if settings.CLOUD_STORAGE_DEPOSITO != "":
        # Leer el contenido del archivo XLSX
        with open(ruta_local_archivo_xlsx, "rb") as archivo:
            # Subir el archivo XLSX a Google Cloud Storage
            try:
                public_url = upload_file_to_gcs(
                    bucket_name=settings.CLOUD_STORAGE_DEPOSITO,
                    blob_name=f"{ruta_gcs}/{nombre_archivo_xlsx}",
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    data=archivo.read(),
                )
                mensaje_gcs = f"Se subio el archivo XLSX a GCS {public_url}"
                bitacora.info(mensaje_gcs)
            except (MyEmptyError, MyBucketNotFoundError, MyFileNotAllowedError, MyFileNotFoundError, MyUploadError) as error:
                mensaje_fallo_gcs = str(error)
                actualizar_quincena_producto(quincena_producto_id, quincena.id, FUENTE, [mensaje_fallo_gcs])
                raise error

    # Si hubo personas sin cuentas, entonces juntarlas para mensajes
    mensajes = []
    if len(personas_sin_cuentas) > 0:
        mensajes.append(f"AVISO: Hubo {len(personas_sin_cuentas)} personas sin cuentas:")
        mensajes += [f"- {p.rfc} {p.nombre_completo}" for p in personas_sin_cuentas]

    # Si hubo mensajes, entonces no es satifactorio
    es_satisfactorio = True
    if len(mensajes) > 0:
        es_satisfactorio = False
        for m in mensajes:
            bitacora.warning(m)

    # Agregar el ultimo mensaje con la cantidad de filas en el archivo XLSX
    mensaje_termino = f"Se generaron {contador} filas"
    mensajes.append(mensaje_termino)

    # Actualizar quincena_producto
    actualizar_quincena_producto(
        quincena_producto_id=quincena_producto_id,
        quincena_id=quincena.id,
        fuente=FUENTE,
        mensajes=mensajes,
        archivo=nombre_archivo_xlsx,
        url=public_url,
        es_satisfactorio=es_satisfactorio,
    )

    # Entregar mensaje de termino
    mensaje_termino = f"Crear dispersiones pensionados: {mensaje_termino}"
    bitacora.info(mensaje_termino)
    return mensaje_termino
