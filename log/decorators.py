
import logging

def addClassLogger(cls: type, log_var='__log'):
    cls_attr = '_{}{}'.format(cls.__name__, log_var)
    setattr(cls, cls_attr, logging.getLogger(cls.__module__ + '.' + cls.__name__))
    return cls