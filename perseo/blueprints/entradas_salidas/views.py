"""
Entradas-Salidas
"""

from flask import Blueprint, render_template, request, url_for
from flask_login import login_required

from lib.datatables import get_datatable_parameters, output_datatable_json
from lib.safe_string import safe_email
from perseo.blueprints.entradas_salidas.models import EntradaSalida
from perseo.blueprints.permisos.models import Permiso
from perseo.blueprints.usuarios.decorators import permission_required
from perseo.blueprints.usuarios.models import Usuario

MODULO = "ENTRADAS SALIDAS"

entradas_salidas = Blueprint("entradas_salidas", __name__, template_folder="templates")


@entradas_salidas.before_request
@login_required
@permission_required(MODULO, Permiso.VER)
def before_request():
    """Permiso por defecto"""


@entradas_salidas.route("/entradas_salidas/datatable_json", methods=["GET", "POST"])
def datatable_json():
    """DataTable JSON para listado de Entradas-Salidas"""
    # Tomar parámetros de Datatables
    draw, start, rows_per_page = get_datatable_parameters()
    # Consultar
    consulta = EntradaSalida.query
    # Primero filtrar por columnas propias
    if "estatus" in request.form:
        consulta = consulta.filter_by(estatus=request.form["estatus"])
    else:
        consulta = consulta.filter_by(estatus="A")
    if "usuario_id" in request.form:
        consulta = consulta.filter_by(usuario_id=request.form["usuario_id"])
    # Luego filtrar por columnas de otras tablas
    if "usuario_email" in request.form:
        try:
            usuario_email = safe_email(request.form["usuario_email"], search_fragment=True)
            if usuario_email != "":
                consulta = consulta.join(Usuario).filter(Usuario.email.contains(usuario_email))
        except ValueError:
            pass
    # Ordenar y paginar
    registros = consulta.order_by(EntradaSalida.id.desc()).offset(start).limit(rows_per_page).all()
    total = consulta.count()
    # Elaborar datos para DataTable
    data = []
    for resultado in registros:
        data.append(
            {
                "creado": resultado.creado.strftime("%Y-%m-%d %H:%M:%S"),
                "tipo": resultado.tipo,
                "usuario": {
                    "email": resultado.usuario.email,
                    "url": url_for("usuarios.detail", usuario_id=resultado.usuario_id),
                },
            }
        )
    # Entregar JSON
    return output_datatable_json(draw, total, data)


@entradas_salidas.route("/entradas_salidas")
def list_active():
    """Listado de Entradas-Salidas activos"""
    return render_template("entradas_salidas/list.jinja2")
