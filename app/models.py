"""Modelo de datos.

Esquema del MVP (una empresa, varias agendas):

    Agenda  1---N  ScheduleRule   -> qué días y en qué franja horaria se atiende
    Agenda  1---N  Closure        -> cortes puntuales (feriados, mantenimiento)
    Agenda  1---N  Booking        -> turnos reservados
    User    1---N  Booking

Los horarios disponibles NO se guardan en la base: se calculan en app/slots.py a
partir de las ScheduleRule menos los Closure menos los Booking existentes. Eso evita
tener que pre-generar millones de filas de slots vacíos.
"""

from datetime import UTC, date, datetime, time
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    TypeDecorator,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UTCDateTime(TypeDecorator):
    """Fecha/hora que SIEMPRE entra y sale como UTC con zona horaria.

    Postgres guarda la zona (timestamptz) pero SQLite no: sin esto, en desarrollo
    los datetimes vuelven de la base sin tzinfo y cualquier comparación contra
    datetime.now(UTC) explota con "can't compare offset-naive and offset-aware".
    Normalizarlo acá evita tener que acordarse en cada consulta.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class BookingStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class Role(StrEnum):
    """Niveles de acceso.

    CLIENTE    reserva y cancela sus propios turnos.
    OPERADOR   planta: ve solo turnos PROGRAMADOS asignados a él; marca
               ABASTECIDO / AUSENTE. Sin admin/coord/config.
    NIVEL_1    coordinación: tablero completo, aeroplantas, horarios, cortes
               y exporta. No toca usuarios.
    NIVEL_2    administrador: todo lo del nivel 1 + alta, nivel y bloqueo de usuarios.
    """

    CLIENTE = "cliente"
    OPERADOR = "operador"
    NIVEL_1 = "nivel1"
    NIVEL_2 = "nivel2"


ROLE_LABELS = {
    Role.CLIENTE: "Cliente",
    Role.OPERADOR: "Operador",
    Role.NIVEL_1: "Nivel 1 — Coordinación",
    Role.NIVEL_2: "Nivel 2 — Administrador",
}


class RoleInEmpresa(StrEnum):
    """Rol del usuario dentro de una Empresa (cliente org). Independiente de Role plataforma."""

    ADMIN_EMPRESA = "admin_empresa"
    USUARIO_EMPRESA = "usuario_empresa"


ROLE_IN_EMPRESA_LABELS = {
    RoleInEmpresa.ADMIN_EMPRESA: "Admin empresa",
    RoleInEmpresa.USUARIO_EMPRESA: "Usuario empresa",
}


class InvitacionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"


class Empresa(Base):
    """Organización cliente (operador / flota)."""

    __tablename__ = "empresas"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    members: Mapped[list["User"]] = relationship(back_populates="empresa")
    invitaciones: Mapped[list["InvitacionEmpresa"]] = relationship(
        back_populates="empresa", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    picture: Mapped[str] = mapped_column(String(500), default="")
    # "sub" de Google: identificador estable de la cuenta. Vacío si entró por login de dev.
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)

    # Datos que completa el cliente en su perfil (útiles para contactarlo)
    phone: Mapped[str] = mapped_column(String(40), default="")
    # Texto libre (cliente individual) o espejo del nombre de Empresa cuando está ligado
    company: Mapped[str] = mapped_column(String(160), default="")

    # Membresía formal a Empresa (fase 1: un usuario ↔ una empresa)
    empresa_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role_in_empresa: Mapped[str | None] = mapped_column(String(30), nullable=True)

    role: Mapped[str] = mapped_column(String(20), default=Role.CLIENTE, index=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="user", foreign_keys="Booking.user_id"
    )
    aircraft: Mapped[list["UserAircraft"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", order_by="UserAircraft.matricula_display"
    )
    empresa: Mapped["Empresa | None"] = relationship(back_populates="members")
    invitaciones_enviadas: Mapped[list["InvitacionEmpresa"]] = relationship(
        back_populates="invited_by_user", foreign_keys="InvitacionEmpresa.invited_by"
    )

    @property
    def display_name(self) -> str:
        return self.name or self.email.split("@")[0]

    @property
    def is_admin(self) -> bool:
        """Puede entrar a /admin y /coord (nivel 1 o 2). Operador NO es admin."""
        return self.role in (Role.NIVEL_1, Role.NIVEL_2)

    @property
    def is_operador(self) -> bool:
        """Login de planta: panel /operador únicamente."""
        return self.role == Role.OPERADOR

    @property
    def can_manage_users(self) -> bool:
        """Solo el nivel 2 da de alta usuarios y cambia niveles."""
        return self.role == Role.NIVEL_2

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def is_admin_empresa(self) -> bool:
        return (
            self.empresa_id is not None
            and self.role_in_empresa == RoleInEmpresa.ADMIN_EMPRESA
        )

    @property
    def is_miembro_empresa(self) -> bool:
        return self.empresa_id is not None

    @property
    def role_in_empresa_label(self) -> str:
        if not self.role_in_empresa:
            return ""
        return ROLE_IN_EMPRESA_LABELS.get(self.role_in_empresa, self.role_in_empresa)

    @property
    def empresa_nombre(self) -> str:
        if self.empresa is not None:
            return self.empresa.nombre
        return self.company or ""


class InvitacionEmpresa(Base):
    """Invitación por email a unirse a una Empresa (aceptada al login Google)."""

    __tablename__ = "invitaciones_empresa"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    empresa_id: Mapped[int] = mapped_column(
        ForeignKey("empresas.id", ondelete="CASCADE"), index=True
    )
    invited_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default=InvitacionStatus.PENDING, index=True
    )
    # Rol que recibirá al aceptar (default usuario)
    role_in_empresa: Mapped[str] = mapped_column(
        String(30), default=RoleInEmpresa.USUARIO_EMPRESA
    )
    token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    empresa: Mapped["Empresa"] = relationship(back_populates="invitaciones")
    invited_by_user: Mapped["User | None"] = relationship(
        back_populates="invitaciones_enviadas", foreign_keys=[invited_by]
    )

    __table_args__ = (
        Index("ix_invitaciones_empresa_email_status", "email", "status"),
    )


class Agenda(Base):
    """Una agenda = una sede + un producto (ej: San Fernando - AVGAS 100LL)."""

    __tablename__ = "agendas"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    product: Mapped[str] = mapped_column(String(120), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(String(255), default="")

    description: Mapped[str] = mapped_column(Text, default="")
    # Texto del botón "Información importante"
    important_info: Mapped[str] = mapped_column(Text, default="")

    # --- Reglas de turnos ---
    slot_minutes: Mapped[int] = mapped_column(Integer, default=20)
    # Cuántos turnos simultáneos entran en el mismo horario (surtidores/posiciones)
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    # Anticipación mínima para reservar (no se puede pedir un turno para dentro de 5 minutos)
    lead_time_hours: Mapped[int] = mapped_column(Integer, default=2)
    # Cuántos días hacia adelante se puede reservar
    horizon_days: Mapped[int] = mapped_column(Integer, default=30)
    # Hasta cuántas horas antes se puede cancelar
    cancel_limit_hours: Mapped[int] = mapped_column(Integer, default=2)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    rules: Mapped[list["ScheduleRule"]] = relationship(
        back_populates="agenda", cascade="all, delete-orphan", order_by="ScheduleRule.weekday"
    )
    closures: Mapped[list["Closure"]] = relationship(
        back_populates="agenda", cascade="all, delete-orphan", order_by="Closure.starts_at"
    )
    bookings: Mapped[list["Booking"]] = relationship(back_populates="agenda")

    @property
    def full_name(self) -> str:
        return f"{self.name} - {self.product}" if self.product else self.name


WEEKDAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


class ScheduleRule(Base):
    """Franja de atención semanal: 'los martes de 15:40 a 23:40'.

    weekday sigue la convención de Python: 0 = lunes ... 6 = domingo.
    Una agenda puede tener varias reglas el mismo día (ej: mañana y tarde con corte al mediodía).
    """

    __tablename__ = "schedule_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    agenda_id: Mapped[int] = mapped_column(ForeignKey("agendas.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)

    agenda: Mapped["Agenda"] = relationship(back_populates="rules")

    __table_args__ = (
        UniqueConstraint("agenda_id", "weekday", "start_time", name="uq_rule_agenda_day_start"),
    )

    @property
    def weekday_name(self) -> str:
        return WEEKDAYS[self.weekday]


class Closure(Base):
    """Bloqueo de un rango de tiempo: feriado, mantenimiento, corte de servicio."""

    __tablename__ = "closures"

    id: Mapped[int] = mapped_column(primary_key=True)
    agenda_id: Mapped[int] = mapped_column(ForeignKey("agendas.id", ondelete="CASCADE"), index=True)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    reason: Mapped[str] = mapped_column(String(200), default="")

    agenda: Mapped["Agenda"] = relationship(back_populates="closures")



class CoordinacionStatus(StrEnum):
    """Pipeline operativo del panel coordinador (independiente de BookingStatus)."""

    PENDIENTE = "PENDIENTE"
    PROGRAMADO = "PROGRAMADO"
    ABASTECIDO = "ABASTECIDO"
    AUSENTE = "AUSENTE"
    CANCELADO = "CANCELADO"


class OrigenBooking(StrEnum):
    WEB = "WEB"
    MANUAL = "MANUAL"


class Abastecedora(Base):
    """Equipo de abastecimiento (surtidor / cisterna) con grado fijo."""

    __tablename__ = "abastecedoras"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    codigo: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    grado: Mapped[str] = mapped_column(String(40))  # JET A-1 / AVGAS 100LL
    capacidad_l: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agenda_id: Mapped[int | None] = mapped_column(
        ForeignKey("agendas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    # "en taller hoy": listar disabled para hoy, OK dias futuros
    fuera_de_servicio_hasta: Mapped[date | None] = mapped_column(Date, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    agenda: Mapped["Agenda | None"] = relationship()
    bookings: Mapped[list["Booking"]] = relationship(back_populates="abastecedora")




class Hangar(Base):
    """Hangar / posición de estacionamiento por planta (opcional)."""

    __tablename__ = "hangares"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(160))
    agenda_id: Mapped[int | None] = mapped_column(
        ForeignKey("agendas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    capacidad: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    agenda: Mapped["Agenda | None"] = relationship()
    aeronaves: Mapped[list["MatriculaCombustible"]] = relationship(back_populates="hangar")


class Operador(Base):
    """Maestro de operadores asignables (Turnera). No es cuenta de login.

    user_id opcional enlaza a un User con role=operador cuando exista cuenta Google.
    agenda_id NULL = Global (todas las plantas); set = acotado a esa Agenda.
    """

    __tablename__ = "operadores"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(160))
    # Clave de upsert: NFKC + casefold + espacios colapsados
    nombre_norm: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    agenda_id: Mapped[int | None] = mapped_column(
        ForeignKey("agendas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, unique=True
    )

    agenda: Mapped["Agenda | None"] = relationship()
    user: Mapped["User | None"] = relationship()
    bookings: Mapped[list["Booking"]] = relationship(back_populates="operador")


class MatriculaCombustible(Base):
    """Listado matricula -> combustible conocido (maestro / ABASTECIDO)."""

    __tablename__ = "matriculas_combustible"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Clave normalizada: upper, sin espacios ni guiones
    matricula: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    matricula_display: Mapped[str] = mapped_column(String(40), default="")
    combustible: Mapped[str | None] = mapped_column(String(80), nullable=True)
    modelo: Mapped[str] = mapped_column(String(80), default="")
    tipo: Mapped[str] = mapped_column(String(60), default="")
    motor: Mapped[str] = mapped_column(String(60), default="")
    cliente: Mapped[str] = mapped_column(String(200), default="")
    hangar_id: Mapped[int | None] = mapped_column(
        ForeignKey("hangares.id", ondelete="SET NULL"), nullable=True, index=True
    )
    capacidad_l: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )

    hangar: Mapped["Hangar | None"] = relationship(back_populates="aeronaves")


class Booking(Base):
    """Un turno reservado por un cliente."""

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    agenda_id: Mapped[int] = mapped_column(ForeignKey("agendas.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Siempre en UTC. La conversión a hora local se hace al mostrar.
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)

    status: Mapped[str] = mapped_column(String(20), default=BookingStatus.CONFIRMED, index=True)

    # Datos propios del rubro aeronáutico
    aircraft: Mapped[str] = mapped_column(String(40), default="")        # matrícula
    aircraft_model: Mapped[str] = mapped_column(String(60), default="")  # ej: Cessna 172
    flight_number: Mapped[str] = mapped_column(String(20), default="")   # opcional, ej: AR1130
    liters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancelled_by_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    # Marca de recordatorio enviado. Es lo que evita que el cron mande el mismo
    # aviso dos veces si se ejecuta más de una vez dentro de la misma ventana.
    reminder_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # --- Panel coordinador ---
    coordinacion_status: Mapped[str] = mapped_column(
        String(20), default=CoordinacionStatus.PENDIENTE, index=True
    )
    origen: Mapped[str] = mapped_column(String(20), default=OrigenBooking.WEB)
    sobreturno: Mapped[bool] = mapped_column(Boolean, default=False)
    abastecedora_id: Mapped[int | None] = mapped_column(
        ForeignKey("abastecedoras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    operador_id: Mapped[int | None] = mapped_column(
        ForeignKey("operadores.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Legacy / espejo: user_id del Operador enlazado (panel /operador y migraciones)
    operador_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    combustible_declarado: Mapped[str] = mapped_column(String(80), default="")
    primera_carga: Mapped[bool] = mapped_column(Boolean, default=False)
    unknown_matricula: Mapped[bool] = mapped_column(Boolean, default=False)
    # Warning UX (no candado): matrícula del maestro con cliente ≠ empresa del solicitante
    matricula_otra_empresa: Mapped[bool] = mapped_column(Boolean, default=False)
    combustible_reconfirmado_en_persona: Mapped[bool] = mapped_column(Boolean, default=False)
    reconfirm_pregunte_en_persona: Mapped[bool] = mapped_column(Boolean, default=False)
    reconfirm_coincide_declarado: Mapped[bool] = mapped_column(Boolean, default=False)
    reconfirmado_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reconfirmado_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    abastecido_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ausente_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ausente_motivo: Mapped[str] = mapped_column(String(300), default="")
    cancelado_coord_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cancelado_motivo: Mapped[str] = mapped_column(String(300), default="")
    asignado_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    asignado_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    agenda: Mapped["Agenda"] = relationship(back_populates="bookings")
    user: Mapped["User"] = relationship(back_populates="bookings", foreign_keys=[user_id])
    abastecedora: Mapped["Abastecedora | None"] = relationship(back_populates="bookings")
    operador: Mapped["Operador | None"] = relationship(
        back_populates="bookings", foreign_keys=[operador_id]
    )
    operador_user: Mapped["User | None"] = relationship(foreign_keys=[operador_user_id])

    __table_args__ = (
        # Acelera el conteo de ocupación por agenda y semana
        Index("ix_bookings_agenda_start_status", "agenda_id", "starts_at", "status"),
    )

    @property
    def is_confirmed(self) -> bool:
        return self.status == BookingStatus.CONFIRMED


class UserAircraft(Base):
    """Aeronave en la lista personal del usuario (Mis Aeronaves).

    Comodidad de cuenta: no es candado de ownership. Cualquiera puede pedir
    turno con cualquier matrícula válida. Quitar de esta lista NO borra el
    maestro MatriculaCombustible.
    """

    __tablename__ = "user_aircraft"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Clave normalizada (upper, alfanum) — alineada a MatriculaCombustible.matricula
    matricula: Mapped[str] = mapped_column(String(40))
    matricula_display: Mapped[str] = mapped_column(String(40), default="")
    modelo: Mapped[str] = mapped_column(String(60), default="")
    tipo: Mapped[str] = mapped_column(String(60), default="")
    # Grado/combustible preferido; si el maestro tiene uno, se reutiliza al alta
    combustible: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="aircraft")

    __table_args__ = (
        UniqueConstraint("user_id", "matricula", name="uq_user_aircraft_user_matricula"),
    )


class TomaFoto(Base):
    """Foto de boca de carga / toma — dataset por matrícula (sin vision)."""

    __tablename__ = "toma_fotos"

    id: Mapped[int] = mapped_column(primary_key=True)
    matricula: Mapped[str] = mapped_column(String(40), index=True)
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    booking: Mapped["Booking | None"] = relationship()
    user: Mapped["User"] = relationship()


class FuelScanLog(Base):
    """Bitácora: operario escaneó QR / confirmó producto a cargar (fail-closed)."""

    __tablename__ = "fuel_scan_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    matricula: Mapped[str] = mapped_column(String(40), default="", index=True)
    product_shown: Mapped[str] = mapped_column(String(80), default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    booking: Mapped["Booking | None"] = relationship()
    user: Mapped["User"] = relationship()
