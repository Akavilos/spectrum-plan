"""Run the real NoneBot entry with a fixture model and a fresh, temporary data directory."""
import os
import sys
sys.path.insert(0,'/app')
sys.path.insert(0,'/app/tests')
from test_integration import FixtureModel, enable
import bot
bot.host.provider=FixtureModel()
bot.host.config['enabled']=True
bot.host.config['operators']=['10001']
enable(bot.system.spectrum,'enabled',True)
enable(bot.system.spectrum,'scope','all')
enable(bot.system.prism,'enabled',False)
enable(bot.system.guard,'enabled',True)
if __name__=='__main__':bot.nonebot.run()
