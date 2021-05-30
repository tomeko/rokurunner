from flask import Flask, render_template, jsonify, request
import os, json
from time import sleep
from enum import IntEnum
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import relationship
from sqlalchemy.sql.schema import ForeignKey
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.types import PickleType
from flask_migrate import Migrate
from urllib.parse import quote_plus
from flask_executor import Executor
from discovery import discover
import requests

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///rokurunner.db"
app.config['EXECUTOR_TYPE'] = 'thread'
app.config['EXECUTOR_MAX_WORKERS'] = 1

app.config['TESTING'] = True

db = SQLAlchemy(app)
migrate = Migrate(app, db)
executor = Executor(app)

class CommandTypes(IntEnum):
    BUTTON_PRESS = 0
    DELAY = 1
    HTTPREQUEST = 2
    CHAR_LIT = 3

class ButtonPressCommands(IntEnum):
    Home = 0
    Rev = 1
    Fwd = 2
    Play = 3
    Select = 4
    Left = 5
    Right = 6
    Down = 7
    Up = 8
    Back = 9
    InstantReplay = 10
    Info = 11
    Backspace = 12
    Search = 13
    Enter = 14
    VolumeDown = 15
    VolumeMute = 16
    VolumeUp = 17
    PowerOff = 18
    InputTuner = 19
    InputHDMI1 = 20
    InputHDMI2 = 21
    InputHDMI3 = 22
    InputHDMI4 = 23
    InputAV1 = 24
    Lit = 25


class RokuDevice(db.Model):
    __tablename__ = "rokudevice"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    ip = db.Column(db.String(16), unique=False, nullable=False)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    runners = db.relationship("Runner", secondary="runners_link")

class Runner(db.Model):
    __tablename__ = "runner"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    cmds = db.Column(
        MutableList.as_mutable(PickleType), default=[]  # List of Command objects
    )  
    rokudevs_using = db.relationship(RokuDevice, secondary="runners_link")

# many to many between runners and roku devices: depracated
runners_link = db.Table(
    "runners_link",
    db.Column("dev_id", db.Integer, db.ForeignKey("rokudevice.id"), primary_key=True),
    db.Column(
        "runner_id", db.Integer, db.ForeignKey("runner.id"), primary_key=True
    ),
)

class RunnerEndpoint(db.Model):
    __tablename__ = "runner_endpoint"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    dev_id = db.Column(db.Integer, unique=False, nullable=False)
    runner_id = db.Column(db.Integer, unique=False, nullable=False)

# this is pickled in the Runner class as cmds
class Command:
    def __init__(self, type, arg):
        self.type = type
        self.arg = arg

    def __repr__(self):
        return f"{self.type}"

# quick hacky busy flag to only allow one runner at a time
curr_runner = None

def exec_runner(dev, runner):
    try:
        global curr_runner
        for cmd in runner:
            s = requests.Session()
            if cmd.type == CommandTypes.DELAY:
                delays = float(cmd.arg)/1000
                print(f'sleeping {delays}')
                sleep(delays)
                
            elif cmd.type == CommandTypes.BUTTON_PRESS:
                print(f'buttonpress: {ButtonPressCommands(cmd.arg).name}')
                url = f'http://{dev.ip}:8060/keypress/{ButtonPressCommands(cmd.arg).name}'
                r = s.post(url)
                
            elif cmd.type == CommandTypes.HTTPREQUEST:
                s.post(cmd.arg)
            elif cmd.type == CommandTypes.CHAR_LIT:
                url = f'http://{dev.ip}:8060/keypress/Lit_{quote_plus(cmd.arg[0])}'
                r = s.post(url)
        curr_runner = None
    except Exception as ex:
        print(str(ex))

@app.route("/exec", methods=["GET"])
def exec():
    runner = Runner.query.filter_by(id=request.args.get("runner_id")).first()
    dev = RokuDevice.query.filter_by(id=request.args.get("dev_id")).first()
    global curr_runner
    
    if not curr_runner:
        curr_runner = runner.name
        msg = f"Running {curr_runner}..."
        executor.submit(exec_runner, dev, runner.cmds)
    else:
        msg = f"Busy with {curr_runner}"

    return render_template("exec.html", msg=msg)

@app.route("/save_eps", methods=["POST"])
def save_eps():
    try:
        eps_resp = json.loads(request.data)
        RunnerEndpoint.query.delete()
        for ep in eps_resp:
            new_ep = RunnerEndpoint()
            new_ep.name = ep["name"]
            new_ep.dev_id = ep["dev_id"]
            new_ep.runner_id = ep["runner_id"]
            db.session.add(new_ep)
        db.session.commit()
        return json.dumps({"status" : "OK"})
    except Exception as e:
        return json.dumps({"status": str(e)})

@app.route("/save_devs", methods=["POST"])
def save_devs():
    try:
        devs_resp = json.loads(request.data)
        RokuDevice.query.delete()
        for dev in devs_resp:
            new_dev = RokuDevice()
            new_dev.ip = dev["ip"]
            new_dev.name = dev["name"]
            new_dev.enabled = True
            db.session.add(new_dev)
            
        db.session.commit() 
        return json.dumps({"status" : "OK"})
    except Exception as e:
        return json.dumps({"status" : str(e)})

@app.route("/save_runners", methods=["POST"])
def save_runners():
    try:
        
        runner_resp = json.loads(request.data)
        runners_all = Runner.query.all()

        # first check for any deletions
        for r_loc in runners_all:
            if not any(d["id"] == r_loc.id for d in runner_resp):
                db.session.delete(r_loc)
        # then check if update or new runner
        for r_resp in runner_resp:
            if r_resp["id"] == -1:
                runner = Runner()
                runner.name = r_resp["name"]
                runner.cmds = []
                db.session.add(runner)
            else:
                r_loc = Runner.query.filter_by(id = r_resp["id"]).first()
                if r_loc:
                    r_loc.name = r_resp["name"]
            
        db.session.commit()

        return json.dumps({"status" : "OK"})
    except Exception as e:
        return json.dumps({"status" : str(e)})

@app.route("/", methods=["GET"])
def home():
    
    if request.method == "GET":
        runners_all = Runner.query.all()
        devices_all = RokuDevice.query.all()
        ep_all = RunnerEndpoint.query.all()
        devs_list = []
        runner_list = []
        ep_list = []
        for dev in devices_all:
            devs_list.append({"name" : dev.name, "id" : dev.id, "ip" : dev.ip })
        for runner in runners_all:
            runner_list.append({"name" : runner.name, "id": runner.id})
        for ep in ep_all:
            ep_list.append({"name" : ep.name, "dev_id": ep.dev_id, "runner_id": ep.runner_id})
        return render_template("home.html", devs=devs_list, cmds=runner_list, eps=ep_list)

@app.route("/roku_list")
def roku_list():
    rokus = discover()
    ret = []
    for r in rokus:
        ret.append({ "ip" : r.location, "usn" : r.usn})
    return json.dumps(ret)

@app.route("/runner_edit", methods=["GET", "POST"])
def runner_edit():
    try:
        
        if request.method == "POST":
            resp = {"status" : "OK"}
            # update runner
            dat = json.loads(request.data)
            id_dict = dat[0]
            cmds_id = id_dict["cmds_id"]
            cmds_arr = dat[1]
            if (len(cmds_arr) == 0):
                resp["status"] = "ErrNoRunnersSent"
                return json.dumps(resp)
            runner = Runner.query.filter_by(id = cmds_id).first()
            if (runner is None):
                resp["status"] = "ErrRunnerNotFound"
                return json.dumps(resp)
            
            runner.cmds.clear()
            for c in cmds_arr:
                newc = Command(c["cmd"], c["arg"])
                runner.cmds.append(newc)

            db.session.commit()
            return json.dumps(resp)
        else:
            runner = Runner.query.filter_by(id=request.args.get("runner_id")).first()
            if runner is None:
                return "runner not found"
            else:
                runner_dat = []
                for cmd in runner.cmds:
                    dict = {"cmd": int(cmd.type), "arg": cmd.arg}
                    runner_dat.append(dict)
                return render_template("runner_edit.html", cmds_id=runner.id, cmds_name=runner.name, cmds_dat=json.dumps(runner_dat))
    except Exception as e:
        return str(e)

if __name__ == "__main__":
    os.system("flask db init")
    os.system("flask db migrate")
    os.system("flask db upgrade")
    app.run(host="0.0.0.0", port=8900, debug=True)

