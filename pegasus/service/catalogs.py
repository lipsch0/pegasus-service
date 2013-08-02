import os
import re
import sys
import shutil
from datetime import datetime

from flask import g, url_for, make_response, request, send_file, json

from sqlalchemy.exc import IntegrityError

from pegasus.service import app, db
from pegasus.service.command import ClientCommand, CompoundCommand
from pegasus.service.api import *

SC_FORMATS = ["xml3","xml4"]
TC_FORMATS = ["file","text"]
RC_FORMATS = ["file","regex"]

FORMATS = {
    "replica": RC_FORMATS,
    "transformation": TC_FORMATS,
    "site": SC_FORMATS
}

def validate_catalog_name(name):
    if name is None:
        raise APIError("Specify catalog name")
    if len(name) >= 100:
        raise APIError("Catalog name too long: %d" % len(name))
    if ".." in name or re.match(r"\A[a-zA-Z0-9.]+\Z", name) is None:
        raise APIError("Invalid catalog name: %s" % name)
    return name

def validate_catalog_format(catalog_type, format):
    if catalog_type not in FORMATS:
        raise APIError("Invalid catalog type: %s" % catalog_type)
    if format not in FORMATS[catalog_type]:
        raise APIError("Invalid %s catalog format: %s" % (catalog_type, format))
    return format

class CatalogMixin:
    def set_name(self, name):
        self.name = validate_catalog_name(name)

    def set_created(self):
        self.created = datetime.now()

    def set_format(self, format):
        self.format = validate_catalog_format(self.__catalog_type__, format)

class ReplicaCatalog(CatalogMixin, db.Model):
    __tablename__ = 'replica_catalog'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'name'),
        {'mysql_engine':'InnoDB'}
    )
    __catalog_type__ = 'replica'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    format = db.Column(db.Enum(*RC_FORMATS), nullable=False)
    created = db.Column(db.DateTime, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    def __init__(self, user_id, name, format):
        self.user_id = user_id
        self.set_name(name)
        self.set_format(format)
        self.set_created()

class SiteCatalog(db.Model, CatalogMixin):
    __tablename__ = 'site_catalog'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'name'),
        {'mysql_engine':'InnoDB'}
    )
    __catalog_type__ = 'site'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    format = db.Column(db.Enum(*SC_FORMATS), nullable=False)
    created = db.Column(db.DateTime, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    def __init__(self, user_id, name, format):
        self.user_id = user_id
        self.set_name(name)
        self.set_format(format)
        self.set_created()

class TransformationCatalog(db.Model, CatalogMixin):
    __tablename__ = 'transformation_catalog'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'name'),
        {'mysql_engine':'InnoDB'}
    )
    __catalog_type__ = 'transformation'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    format = db.Column(db.Enum(*TC_FORMATS), nullable=False)
    created = db.Column(db.DateTime, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    def __init__(self, user_id, name, format):
        self.user_id = user_id
        self.set_name(name)
        self.set_format(format)
        self.set_created()

def catalog_object(catalog_type, c):
    return {
        "id": c.id,
        "name": c.name,
        "created": c.created,
        "format": c.format,
        "href": url_for("route_get_catalog", catalog_type=catalog_type, name=c.name, _external=True)
    }

def get_catalog_model(catalog_type):
    if catalog_type == "replica":
        return ReplicaCatalog
    elif catalog_type == "site":
        return SiteCatalog
    elif catalog_type == "transformation":
        return TransformationCatalog
    else:
        raise APIError("Invalid catalog type: %s" % catalog_type, status_code=400)

def get_catalog_path(catalog_type, user_id, name):
    dirname = os.path.join(app.config["STORAGE_DIR"],
                           "userdata", str(user_id),
                           "catalogs", catalog_type)
    if not os.path.exists(dirname): os.makedirs(dirname)
    return os.path.join(dirname, name)

def get_catalog(catalog_type, user_id, name):
    Catalog = get_catalog_model(catalog_type)
    return Catalog.query.filter_by(user_id=g.user.id, name=name).one()

def list_catalogs(catalog_type, user_id):
    Catalog = get_catalog_model(catalog_type)
    return Catalog.query.filter_by(user_id=user_id).all()

def save_catalog(catalog_type, user_id, name, format, file):
    Catalog = get_catalog_model(catalog_type)

    try:
        cat = Catalog(user_id, name, format)
        db.session.add(cat)
        db.session.flush()
    except IntegrityError, e:
        raise APIError("Duplicate catalog name")

    save_catalog_file(catalog_type, g.user.id, name, file)

def save_catalog_file(catalog_type, user_id, name, file):
    filename = get_catalog_path(catalog_type, user_id, name)

    if os.path.exists(filename):
        os.remove(filename)

    f = open(filename, "wb")
    try:
        shutil.copyfileobj(file, f)
    finally:
        f.close()

@app.route("/catalogs/", methods=["GET"])
def route_all_catalogs():
    result = {
        "site": url_for("route_list_catalogs", catalog_type="site", _external=True),
        "replica": url_for("route_list_catalogs", catalog_type="replica", _external=True),
        "transformation": url_for("route_list_catalogs", catalog_type="transformation", _external=True)
    }
    return json_response(result)

@app.route("/catalogs/<string:catalog_type>/", methods=["GET"])
def route_list_catalogs(catalog_type):
    clist = list_catalogs(catalog_type, g.user.id)
    result = [catalog_object(catalog_type, c) for c in clist]
    return json_response(result)

@app.route("/catalogs/<string:catalog_type>/", methods=["POST"])
def route_store_catalog(catalog_type):

    # The name of the catalog
    name = request.form.get("name", None)
    if name is None:
        raise APIError("Specify name")

    # The format of the catalog
    format = request.form.get("format", None)
    if format is None:
        raise APIError("Specify format")

    # The catalog file
    file = request.files.get("file", None)
    if file is None:
        raise APIError("Specify file")

    save_catalog(catalog_type, g.user.id, name, format, file)

    db.session.commit()

    return json_created(url_for("route_get_catalog", catalog_type=catalog_type, name=name, _external=True))

@app.route("/catalogs/<string:catalog_type>/<string:name>", methods=["GET"])
def route_get_catalog(catalog_type, name):
    filename = get_catalog_path(catalog_type, g.user.id, name)

    if not os.path.exists(filename):
        raise APIError("No such catalog: %s" % name, 404)

    return send_file(filename, mimetype="text/plain")

@app.route("/catalogs/<string:catalog_type>/<string:name>", methods=["DELETE"])
def route_delete_catalog(catalog_type, name):
    c = get_catalog(catalog_type, g.user.id, name)

    db.session.delete(c)

    # Update the database before removing the file
    # so that we can be sure the database changes
    # will go through before removing the file.
    db.session.flush()

    filename = get_catalog_path(catalog_type, g.user.id, name)
    if os.path.exists(filename):
        os.remove(filename)

    db.session.commit()

    return json_response({"message":"deleted"})

@app.route("/catalogs/<string:catalog_type>/<string:name>", methods=["PUT"])
def route_update_catalog(catalog_type, name):

    c = get_catalog(catalog_type, g.user.id, name)

    # Update created date
    c.set_created()

    format = request.form.get("format", None)
    if format is not None:
        c.set_format(format)

    # Update the database before updating the file
    # so that we can be sure the database updates
    # go through before messing with the file
    db.session.flush()

    # Update the file contents
    file = request.files.get("file", None)
    if file is not None:
        # Update the file
        save_catalog_file(catalog_type, g.user.id, name, file)

    db.session.commit()

    return json_response(catalog_object(catalog_type, c))

class ListCommand(ClientCommand):
    description = "List stored catalogs"
    usage = "Usage: %prog list [options] TYPE"

    def run(self):
        if len(self.args) == 0:
            self.parser.error("Specify TYPE")
        elif len(self.args) > 1:
            self.parser.error("Invalid argument")

        catalog_type = self.args[0]

        response = self.get("/catalogs/%s" % catalog_type)
        result = response.json()

        if response.status_code != 200:
            print "ERROR:",result["message"]
            exit(1)

        fmt = "%-20s %-8s %-32s %s"
        if len(result) > 0:
            print fmt % ("NAME","FORMAT","CREATED","URL")
        for r in result:
            print fmt % (r["name"], r["format"], r["created"], r["href"])

class UploadCommand(ClientCommand):
    description = "Upload a catalog to the server"
    usage = "Usage: %prog create [options] TYPE NAME FORMAT FILE"

    def run(self):
        if len(self.args) == 0:
            self.parser.error("Specify arguments")
        elif len(self.args) > 4:
            self.parser.error("Invalid argument")

        catalog_type = self.args[0]
        name = self.args[1]
        format = self.args[2]
        file = self.args[3]

        data = {"name": name, "format": format}
        files = {"file": open(file, "rb")}
        response = self.post("/catalogs/%s/" % catalog_type, data=data, files=files)
        if response.status_code != 201:
            result = response.json()
            print "ERROR:",result["message"]
            exit(1)

class UpdateCommand(ClientCommand):
    description = "Update a catalog"
    usage = "Usage: %prog update TYPE NAME FORMAT FILE"

    def run(self):
        if len(self.args) == 0:
            self.parser.error("Specify arguments")
        elif len(self.args) > 4:
            self.parser.error("Invalid argument")

        catalog_type = self.args[0]
        name = self.args[1]
        format = self.args[2]
        file = self.args[3]

        data = {"format": format}
        files = {"file": open(file, "rb")}
        response = self.put("/catalogs/%s/%s" % (catalog_type, name), data=data, files=files)
        if response.status_code != 200:
            result = response.json()
            print "ERROR:",result["message"]
            exit(1)

class DeleteCommand(ClientCommand):
    description = "Delete a catalog"
    usage = "Usage: %prog delete TYPE NAME"

    def run(self):
        if len(self.args) == 0:
            self.parser.error("Specify TYPE and NAME")
        if len(self.args) > 2:
            self.parser.error("Invalid argument")

        catalog_type = self.args[0]
        name = self.args[1]

        response = self.delete("/catalogs/%s/%s" % (catalog_type, name))
        result = response.json()
        if response.status_code != 200:
            print "ERROR:",result["message"]
            exit(1)

class DownloadCommand(ClientCommand):
    description = "Download a catalog"
    usage = "Usage: %prog download TYPE NAME"

    def run(self):
        if len(self.args) == 0:
            self.parser.error("Specify TYPE and NAME")
        if len(self.args) > 2:
            self.parser.error("Invalid argument")

        catalog_type = self.args[0]
        name = self.args[1]

        response = self.get("/catalogs/%s/%s" % (catalog_type, name), stream=True)
        if response.status_code != 200:
            result = response.json()
            print "ERROR:",result["message"]
            exit(1)

        for chunk in response:
            sys.stdout.write(chunk)

class CatalogCommand(CompoundCommand):
    description = "Client for catalog management"
    commands = {
        "list": ListCommand,
        "upload": UploadCommand,
        "download": DownloadCommand,
        "update": UpdateCommand,
        "delete": DeleteCommand
    }

def main():
    "The entry point for pegasus-service-catalogs"
    CatalogCommand().main()
